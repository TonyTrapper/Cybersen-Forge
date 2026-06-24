package main

import (
	"strings"
	"testing"
)

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
	args := sandboxArguments(config, `echo ok && ls -la`)
	joined := strings.Join(args, " ")
	for _, required := range []string{"--network=none", "--read-only", "--cap-drop=all", "--security-opt=no-new-privileges", "/bin/sh -lc"} {
		if !strings.Contains(joined, required) {
			t.Fatalf("sandbox arguments missing %q: %s", required, joined)
		}
	}
	if args[len(args)-1] != `echo ok && ls -la` {
		t.Fatalf("sandbox command was not passed as one argument: %#v", args)
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
