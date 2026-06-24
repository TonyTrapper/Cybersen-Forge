package main

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func TestPersistentShellKeepsState(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("helper runtime uses a POSIX shell")
	}
	temporary := t.TempDir()
	fakeRuntime := filepath.Join(temporary, "fake-container-runtime")
	script := `#!/bin/sh
if [ "$1" = "rm" ]; then
  exit 0
fi
exec /bin/sh
`
	if err := os.WriteFile(fakeRuntime, []byte(script), 0700); err != nil {
		t.Fatal(err)
	}
	originalTimeout := commandTimeout
	commandTimeout = 3 * time.Second
	defer func() { commandTimeout = originalTimeout }()

	config := sandboxConfig{
		Enabled: true, Available: true, Runtime: fakeRuntime,
		Image: "test", Workspace: temporary,
	}
	shell := newPersistentShell(config, "agt_test")
	defer shell.Close()

	if result := shell.Run("cd /tmp"); result.ExitCode != 0 {
		t.Fatalf("cd failed: %#v", result)
	}
	if result := shell.Run("pwd"); result.ExitCode != 0 || strings.TrimSpace(result.Stdout) != "/tmp" {
		t.Fatalf("working directory was not preserved: %#v", result)
	}
	if result := shell.Run("export FORGE_TEST=Cybersen"); result.ExitCode != 0 {
		t.Fatalf("export failed: %#v", result)
	}
	if result := shell.Run(`printf "%s" "$FORGE_TEST"`); result.ExitCode != 0 || result.Stdout != "Cybersen" {
		t.Fatalf("environment was not preserved: %#v", result)
	}
}

func TestAllowedHostCommands(t *testing.T) {
	commands := []string{
		"hostname",
		"whoami",
		"id -un",
		"uname -a",
		"ip addr",
		"ip route show",
		"ss -lntup",
		"ps aux",
		"df -h /",
		"free -h",
		"cat /etc/os-release",
		"ls -la /tmp",
		"echo Cybersen",
	}
	for _, command := range commands {
		args, err := splitCommandLine(command)
		if err != nil {
			t.Fatalf("split %q: %v", command, err)
		}
		if err := validateCommand(args); err != nil {
			t.Errorf("expected %q to be allowed: %v", command, err)
		}
	}
}

func TestRejectedHostCommands(t *testing.T) {
	commands := []string{
		"bash -c id",
		"sh -c whoami",
		"hostname && whoami",
		"cat /etc/shadow",
		"curl https://example.com",
		"ls /root",
		"ip neigh",
	}
	for _, command := range commands {
		args, splitErr := splitCommandLine(command)
		if splitErr == nil {
			if err := validateCommand(args); err == nil {
				t.Errorf("expected %q to be rejected", command)
			}
		}
	}
}

func TestQuotedEcho(t *testing.T) {
	args, err := splitCommandLine(`echo "Forja tu Yugo"`)
	if err != nil {
		t.Fatal(err)
	}
	if len(args) != 2 || args[1] != "Forja tu Yugo" {
		t.Fatalf("unexpected args: %#v", args)
	}
}

func TestSandboxArgumentsAreIsolated(t *testing.T) {
	config := sandboxConfig{
		Runtime: "podman", Image: "alpine:3.20", Workspace: "/tmp/cybersen-forge-workspace",
	}
	args := sandboxShellArguments(config, "cybersen-forge-shell-test")
	joined := strings.Join(args, " ")
	for _, required := range []string{"--network=none", "--read-only", "--cap-drop=all", "--security-opt=no-new-privileges", "--name cybersen-forge-shell-test", "/bin/sh"} {
		if !strings.Contains(joined, required) {
			t.Fatalf("sandbox arguments missing %q: %s", required, joined)
		}
	}
	if args[len(args)-1] != "/bin/sh" {
		t.Fatalf("persistent shell entrypoint is incorrect: %#v", args)
	}
}

func TestWindowsDiagnosticCommands(t *testing.T) {
	commands := [][]string{
		{"hostname"},
		{"whoami", "/all"},
		{"systeminfo"},
		{"ipconfig", "/all"},
		{"tasklist", "/v"},
		{"netstat", "-ano"},
		{"route", "print"},
	}
	for _, args := range commands {
		if err := validateWindowsCommand(args); err != nil {
			t.Errorf("expected %#v to be allowed: %v", args, err)
		}
	}
	for _, args := range [][]string{{"powershell", "-Command", "Get-Process"}, {"cmd", "/c", "whoami"}, {"route", "add"}} {
		if err := validateWindowsCommand(args); err == nil {
			t.Errorf("expected %#v to be rejected", args)
		}
	}
}
