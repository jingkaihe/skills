package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"

	"github.com/jingkaihe/matchlock/pkg/sdk"
	"golang.org/x/term"
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

	sandbox := sdk.New("alpine:latest")

	vmID, err := client.Launch(sandbox)
	if err != nil {
		return fmt.Errorf("launch sandbox: %w", err)
	}
	slog.Info("sandbox ready", "vm", vmID)

	if !term.IsTerminal(int(os.Stdin.Fd())) {
		return errors.New("terminal required: run this example in an interactive terminal")
	}

	ctx := context.Background()
	stdinFD := int(os.Stdin.Fd())

	cols, rows, err := term.GetSize(stdinFD)
	if err != nil {
		rows, cols = 24, 80
	}

	fmt.Println("Connected to sandbox shell. Type 'exit' or press Ctrl-D to quit.")

	oldState, err := term.MakeRaw(stdinFD)
	if err != nil {
		return fmt.Errorf("set raw mode: %w", err)
	}
	restored := false
	defer func() {
		if !restored {
			_ = term.Restore(stdinFD, oldState)
		}
	}()

	resizeCh := make(chan [2]uint16, 4)
	winchCh := make(chan os.Signal, 1)
	stopResize := make(chan struct{})
	signal.Notify(winchCh, syscall.SIGWINCH)
	defer signal.Stop(winchCh)
	defer close(stopResize)

	go func() {
		for {
			select {
			case <-stopResize:
				return
			case <-winchCh:
				c, r, sizeErr := term.GetSize(stdinFD)
				if sizeErr != nil {
					continue
				}
				select {
				case resizeCh <- [2]uint16{uint16(r), uint16(c)}:
				default:
				}
			}
		}
	}()

	ttyResult, err := client.ExecInteractive(ctx, "sh", &sdk.ExecInteractiveOptions{
		WorkingDir: "/tmp",
		Rows:       uint16(rows),
		Cols:       uint16(cols),
		Stdin:      os.Stdin,
		Stdout:     os.Stdout,
		Resize:     resizeCh,
	})
	if err != nil {
		return fmt.Errorf("exec_tty: %w", err)
	}
	if err := term.Restore(stdinFD, oldState); err != nil {
		return fmt.Errorf("restore terminal mode: %w", err)
	}
	restored = true

	fmt.Printf("\nShell exited: code=%d duration_ms=%d\n", ttyResult.ExitCode, ttyResult.DurationMS)

	return nil
}
