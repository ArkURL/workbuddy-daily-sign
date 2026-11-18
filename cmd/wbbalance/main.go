// Command wbbalance prints the available credit balance for every account in accounts.json.
//
// It is intentionally standalone: no third-party packages, one binary, no Python needed at query time.
// Credentials are read from the same local store that tools/extract_local.py writes (gitignored, mode 0600).
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"text/tabwriter"
	"time"
)

const (
	defaultEndpoint      = "https://copilot.tencent.com"
	balancePath          = "/billing/meter/get-user-resource-summary" // no /v2, on purpose
	refreshPath          = "/v2/plugin/auth/token/refresh"
	refreshMarginSeconds = int64(120)
	requestTimeout       = 20 * time.Second
	userAgent            = "WorkBuddy"
)

var errUnauthorized = errors.New("unauthorized")

type state struct {
	UID              string `json:"uid"`
	AccessToken      string `json:"accessToken"`
	RefreshToken     string `json:"refreshToken"`
	AccessExpiresAt  int64  `json:"accessExpiresAt,omitempty"`
	RefreshExpiresAt int64  `json:"refreshExpiresAt,omitempty"`
	Domain           string `json:"domain,omitempty"`
	EnterpriseID     string `json:"enterpriseId,omitempty"`
	Endpoint         string `json:"endpoint,omitempty"`
}

type account struct {
	Name  string `json:"name"`
	Home  string `json:"home,omitempty"`
	State state  `json:"state"`
}

type creditPackage struct {
	Code   string `json:"PackageCode"`
	Total  string `json:"CycleTotalCapacity"`
	Remain string `json:"CycleRemainCapacity"`
	Used   string `json:"CycleUsedCapacity"`
	Unit   string `json:"CapacityUnit"`
}

type summary struct {
	TotalCredits json.Number     `json:"TotalCredits"`
	Packages     []creditPackage `json:"Packages"`
	IsPaidUser   bool            `json:"IsPaidUser"`
}

type apiResponse struct {
	Code int             `json:"code"`
	Msg  string          `json:"msg"`
	Data json.RawMessage `json:"data"`
}

type result struct {
	Name     string  `json:"name"`
	UID      string  `json:"uid"`
	Balance  float64 `json:"balance"`
	Total    float64 `json:"total"`
	Packages int     `json:"packages"`
	Error    string  `json:"error,omitempty"`
}

type refreshData struct {
	AccessToken      string `json:"accessToken"`
	RefreshToken     string `json:"refreshToken"`
	ExpiresIn        int64  `json:"expiresIn"`
	RefreshExpiresIn int64  `json:"refreshExpiresIn"`
	Domain           string `json:"domain"`
}

func main() {
	store := flag.String("store", "", "accounts.json 路径（默认 ./accounts.json，或环境变量 WORKBUDDY_ACCOUNTS_FILE）")
	filter := flag.String("name", "", "只查询指定账号（name 字段）")
	asJSON := flag.Bool("json", false, "输出 JSON 而不是表格")
	verbose := flag.Bool("verbose", false, "显示每个积分包的明细")
	flag.Parse()

	if err := run(*store, *filter, *asJSON, *verbose); err != nil {
		fmt.Fprintln(os.Stderr, "wbbalance:", err)
		os.Exit(1)
	}
}

func run(storeFlag, filter string, asJSON, verbose bool) error {
	path, fromEnv, err := resolveStore(storeFlag)
	if err != nil {
		return err
	}
	accounts, fromEnv, err := loadAccounts(path)
	if err != nil {
		return err
	}
	if len(accounts) == 0 {
		return errors.New("账本里没有账号，先跑 tools/extract_local.py 导出")
	}

	changed := false
	results := make([]result, 0, len(accounts))
	for i := range accounts {
		acc := &accounts[i]
		if filter != "" && acc.Name != filter {
			continue
		}
		st := &acc.State
		if st.Endpoint == "" {
			st.Endpoint = defaultEndpoint
		}
		if st.AccessToken == "" {
			results = append(results, result{Name: acc.Name, UID: shortUID(st.UID), Error: "缺少 accessToken"})
			continue
		}

		if needsRefresh(st) {
			if err := refresh(st); err != nil {
				results = append(results, result{Name: acc.Name, UID: shortUID(st.UID), Error: "续期失败：" + err.Error()})
				continue
			}
			changed = true
		}

		sum, err := queryBalance(st)
		if errors.Is(err, errUnauthorized) && st.RefreshToken != "" {
			if refreshErr := refresh(st); refreshErr == nil {
				changed = true
				sum, err = queryBalance(st)
			}
		}
		if err != nil {
			results = append(results, result{Name: acc.Name, UID: shortUID(st.UID), Error: err.Error()})
			continue
		}

		results = append(results, result{
			Name:     acc.Name,
			UID:      shortUID(st.UID),
			Balance:  toFloat(sum.TotalCredits),
			Total:    sumTotal(sum.Packages),
			Packages: len(sum.Packages),
		})
		if verbose {
			fmt.Printf("  [%s]\n", acc.Name)
			tw := tabwriter.NewWriter(os.Stdout, 0, 4, 2, ' ', 0)
			fmt.Fprintln(tw, "  积分包\t剩余\t已用\t额度")
			for _, p := range sum.Packages {
				fmt.Fprintf(tw, "  %s\t%s\t%s\t%s\n", p.Code, p.Remain, p.Used, p.Total)
			}
			tw.Flush()
		}
	}

	if changed && !fromEnv && path != "" {
		if err := saveAccounts(path, accounts); err != nil {
			fmt.Fprintln(os.Stderr, "wbbalance: 轮换后的凭据写回失败：", err)
		}
	}

	if asJSON {
		return json.NewEncoder(os.Stdout).Encode(results)
	}

	tw := tabwriter.NewWriter(os.Stdout, 0, 4, 2, ' ', 0)
	fmt.Fprintln(tw, "账号\tuid\t可用积分\t总额度\t积分包数")
	failed := false
	for _, r := range results {
		if r.Error != "" {
			failed = true
			fmt.Fprintf(tw, "%s\t%s\tERROR\t-\t%s\n", r.Name, r.UID, r.Error)
			continue
		}
		fmt.Fprintf(tw, "%s\t%s\t%.2f\t%.2f\t%d\n", r.Name, r.UID, r.Balance, r.Total, r.Packages)
	}
	tw.Flush()
	if failed {
		os.Exit(1)
	}
	return nil
}

func resolveStore(explicit string) (string, bool, error) {
	if explicit != "" {
		return expandHome(explicit), false, nil
	}
	if envPath := os.Getenv("WORKBUDDY_ACCOUNTS_FILE"); envPath != "" {
		return expandHome(envPath), false, nil
	}
	home, err := os.UserHomeDir()
	if err == nil {
		configPath := filepath.Join(home, ".config", "workbuddy-daily-sign", "accounts.json")
		if _, statErr := os.Stat(configPath); statErr == nil {
			return configPath, false, nil
		}
		// 新位置还没有、老仓库里的账本还在时继续用老位置，避免升级后突然查不到
		if _, statErr := os.Stat("accounts.json"); statErr != nil {
			return configPath, false, nil
		}
	}
	return "accounts.json", false, nil
}

func expandHome(path string) string {
	if path == "~" || strings.HasPrefix(path, "~/") {
		home, err := os.UserHomeDir()
		if err == nil {
			return filepath.Join(home, strings.TrimPrefix(strings.TrimPrefix(path, "~"), "/"))
		}
	}
	return path
}

func loadAccounts(path string) ([]account, bool, error) {
	raw, err := os.ReadFile(path)
	fromEnv := false
	if err != nil {
		if !os.IsNotExist(err) {
			return nil, false, err
		}
		if envJSON := os.Getenv("WORKBUDDY_ACCOUNTS"); envJSON != "" {
			raw = []byte(envJSON)
			fromEnv = true
		} else {
			return nil, false, fmt.Errorf("找不到账本 %s（可用 --store 或 WORKBUDDY_ACCOUNTS_FILE 指定；也可用 WORKBUDDY_ACCOUNTS）", path)
		}
	}
	var accounts []account
	if err := json.Unmarshal(raw, &accounts); err != nil {
		return nil, fromEnv, fmt.Errorf("账本不是合法的 JSON 数组：%w", err)
	}
	return accounts, fromEnv, nil
}

func saveAccounts(path string, accounts []account) error {
	tmp := path + ".tmp"
	file, err := os.OpenFile(tmp, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0600)
	if err != nil {
		return err
	}
	encoder := json.NewEncoder(file)
	encoder.SetEscapeHTML(false)
	err = encoder.Encode(accounts)
	closeErr := file.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	if err := os.Chmod(tmp, 0600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func needsRefresh(st *state) bool {
	if st.AccessExpiresAt <= 0 {
		return false
	}
	return time.Now().Unix() >= st.AccessExpiresAt-refreshMarginSeconds
}

func headers(st *state, extra map[string]string) http.Header {
	h := http.Header{}
	h.Set("Accept", "application/json")
	h.Set("Content-Type", "application/json")
	h.Set("User-Agent", userAgent)
	if st.AccessToken != "" {
		h.Set("Authorization", "Bearer "+st.AccessToken)
	}
	if st.Domain != "" {
		h.Set("X-Domain", st.Domain)
	}
	if st.EnterpriseID != "" {
		h.Set("X-Enterprise-Id", st.EnterpriseID)
		h.Set("X-Tenant-Id", st.EnterpriseID)
	}
	for key, value := range extra {
		h.Set(key, value)
	}
	return h
}

func post(url string, h http.Header, payload []byte) (int, []byte, error) {
	req, err := http.NewRequest(http.MethodPost, url, bytes.NewReader(payload))
	if err != nil {
		return 0, nil, err
	}
	req.Header = h
	client := &http.Client{Timeout: requestTimeout}
	resp, err := client.Do(req)
	if err != nil {
		return 0, nil, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	return resp.StatusCode, body, err
}

func refresh(st *state) error {
	if st.RefreshToken == "" {
		return errors.New("没有 refreshToken，无法续期")
	}
	if st.RefreshExpiresAt > 0 && time.Now().Unix() >= st.RefreshExpiresAt {
		return errors.New("refreshToken 已过期，请重新导出")
	}
	extra := map[string]string{
		"X-Refresh-Token":       st.RefreshToken,
		"X-Auth-Refresh-Source": "plugin",
	}
	refreshHeaders := headers(st, extra)
	refreshHeaders.Del("Authorization") // 续期只认 X-Refresh-Token
	code, body, err := post(st.Endpoint+refreshPath, refreshHeaders, []byte("{}"))
	if err != nil {
		return err
	}
	if code != http.StatusOK {
		return fmt.Errorf("续期返回 HTTP %d", code)
	}
	var raw apiResponse
	if err := json.Unmarshal(body, &raw); err != nil {
		return err
	}
	var data refreshData
	if err := json.Unmarshal(raw.Data, &data); err != nil {
		return err
	}
	if data.AccessToken == "" {
		return errors.New("续期响应里没有 accessToken")
	}
	now := time.Now().Unix()
	st.AccessToken = data.AccessToken
	if data.RefreshToken != "" {
		st.RefreshToken = data.RefreshToken
	}
	if data.ExpiresIn > 0 {
		st.AccessExpiresAt = now + data.ExpiresIn
	}
	if data.RefreshExpiresIn > 0 {
		st.RefreshExpiresAt = now + data.RefreshExpiresIn
	}
	if data.Domain != "" {
		st.Domain = data.Domain
	}
	return nil
}

func queryBalance(st *state) (*summary, error) {
	code, body, err := post(st.Endpoint+balancePath, headers(st, nil), []byte("{}"))
	if err != nil {
		return nil, err
	}
	if code == http.StatusUnauthorized || code == http.StatusForbidden {
		return nil, errUnauthorized
	}
	if code != http.StatusOK {
		return nil, fmt.Errorf("余额接口返回 HTTP %d", code)
	}
	var raw apiResponse
	if err := json.Unmarshal(body, &raw); err != nil {
		return nil, err
	}
	if raw.Code != 0 {
		return nil, fmt.Errorf("余额接口返回 code=%d: %s", raw.Code, raw.Msg)
	}
	var sum summary
	if err := json.Unmarshal(raw.Data, &sum); err != nil {
		return nil, err
	}
	if string(sum.TotalCredits) == "" {
		return nil, errors.New("余额响应里没有 TotalCredits")
	}
	return &sum, nil
}

func toFloat(number json.Number) float64 {
	value, err := strconv.ParseFloat(string(number), 64)
	if err != nil {
		return 0
	}
	return value
}

func sumTotal(packages []creditPackage) float64 {
	var total float64
	for _, p := range packages {
		value, err := strconv.ParseFloat(p.Total, 64)
		if err == nil {
			total += value
		}
	}
	return total
}

func shortUID(uid string) string {
	if len(uid) > 8 {
		return uid[:8]
	}
	return uid
}
