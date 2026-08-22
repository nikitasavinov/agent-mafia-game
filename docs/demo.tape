# Re-record the README demo with: vhs docs/demo.tape

Output docs/assets/everyone-angry.gif

Require uv

Set Shell "bash"
Set FontFamily "Menlo"
Set FontSize 18
Set Width 1200
Set Height 700
Set Padding 18
Set Framerate 12
Set Theme "TokyoNight"
Set WindowBar "Colorful"
Set WindowBarSize 36
Set TypingSpeed 30ms

Hide
Type "unset NO_COLOR"
Enter
Type "export TERM=xterm-256color"
Enter
Type "export COLORTERM=truecolor"
Enter
Type "export FORCE_COLOR=1"
Enter
Type "export UV_CACHE_DIR=/tmp/agent-mafia-uv-cache"
Enter
Type "export PS1='❯ '"
Enter
Type "clear"
Enter
Show

Sleep 1s
Type "uv run mafia play examples/openrouter_but_everyone_angry.example.toml --database /tmp/agent-mafia-demo.db"
Enter
Sleep 120s
