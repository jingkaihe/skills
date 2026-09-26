package main

import (
	"context"
	"fmt"
	"log/slog"
	"maps"
	"os"

	"github.com/jingkaihe/matchlock/pkg/sdk"
)

func main() {
	if err := run(); err != nil {
		slog.Error("fatal", "error", err)
		os.Exit(1)
	}
}

func run() error {
	client, err := sdk.NewClient(sdk.DefaultConfig())
	if err != nil {
		return fmt.Errorf("create client: %w", err)
	}
	defer client.Remove()
	defer client.Close(0)

	apiKey := os.Getenv("ANTHROPIC_API_KEY")

	sandbox := sdk.New("python:3.12-alpine").
		AllowHost(
			"dl-cdn.alpinelinux.org",
			"files.pythonhosted.org", "pypi.org",
			"astral.sh", "github.com", "objects.githubusercontent.com",
			"api.anthropic.com",
		).
		WithNetworkInterception(&sdk.NetworkInterceptionConfig{
			Rules: []sdk.NetworkHookRule{
				{
					Name:  "inject-anthropic-api-key",
					Phase: sdk.NetworkHookPhaseBefore,
					Hosts: []string{"api.anthropic.com"},
					Hook: func(_ context.Context, req sdk.NetworkHookRequest) (*sdk.NetworkHookResult, error) {
						headers := maps.Clone(req.RequestHeaders)
						headers["X-Api-Key"] = []string{apiKey}
						return &sdk.NetworkHookResult{
							Action: sdk.NetworkHookActionMutate,
							Request: &sdk.NetworkHookRequestMutation{
								Headers: headers,
							},
						}, nil
					},
				},
			},
		})

	vmID, err := client.Launch(sandbox)
	if err != nil {
		return fmt.Errorf("launch sandbox: %w", err)
	}
	slog.Info("sandbox ready", "vm", vmID)

	result, err := client.Exec(context.Background(), "python3 --version")
	if err != nil {
		return fmt.Errorf("exec python3 --version: %w", err)
	}
	fmt.Print(result.Stdout)

	if _, err := client.Exec(context.Background(), "pip install --quiet uv"); err != nil {
		return fmt.Errorf("exec pip install uv: %w", err)
	}

	// The script uses a placeholder API key — the network interception hook
	// injects the real x-api-key header on the host side, so the secret
	// never enters the VM.
	script := `# /// script
# requires-python = ">=3.12"
# dependencies = ["anthropic"]
# ///
import anthropic

client = anthropic.Anthropic(api_key="placeholder")
with client.messages.stream(
    model="claude-haiku-4-5-20251001",
    max_tokens=1000,
    messages=[{"role": "user", "content": "Explain TCP to me"}],
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
print()
`
	if err := client.WriteFile(context.Background(), "/workspace/ask.py", []byte(script)); err != nil {
		return fmt.Errorf("write_file: %w", err)
	}

	streamResult, err := client.ExecStream(context.Background(),
		"uv run /workspace/ask.py",
		os.Stdout, os.Stderr,
	)
	if err != nil {
		return fmt.Errorf("exec_stream: %w", err)
	}
	fmt.Println()
	slog.Info("done", "exit_code", streamResult.ExitCode, "duration_ms", streamResult.DurationMS)
	return nil
}
