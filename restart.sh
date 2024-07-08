#!/bin/bash
sessname="xcsoar"

tmux kill-session -t "$sessname"

tmux new-session -d -s "$sessname"

tmux send-keys -t "$sessname" "su dev" ENTER
tmux send-keys -t "$sessname" "cd /home/dev/workspace/xcsoar_server" ENTER
tmux send-keys -t "$sessname" "source .venv/bin/activate" ENTER
tmux send-keys -t "$sessname" "python main.py > log.txt" ENTER