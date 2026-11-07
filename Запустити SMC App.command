#!/bin/zsh
# Подвійний клік у Finder: запускає SMC App і відкриває його в браузері.
[ -f ~/.zshrc ] && source ~/.zshrc   # тут SMC_SHEET_URL / SMC_SHEET_TOKEN
cd "${0:A:h}"
exec python3 app.py
