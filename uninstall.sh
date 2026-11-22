#!/usr/bin/env bash
# rapoodriver4linux - desinstalador
set -euo pipefail

echo "=== rapoodriver4linux - desinstalação ==="

if [[ -f /etc/udev/rules.d/99-rapoo.rules ]]; then
    echo "Removendo regra udev (sudo)..."
    sudo rm -f /etc/udev/rules.d/99-rapoo.rules
    sudo udevadm control --reload-rules && sudo udevadm trigger || true
fi

if [[ -L "$HOME/.local/bin/rapooctl" ]]; then
    echo "Removendo comando rapooctl..."
    rm -f "$HOME/.local/bin/rapooctl"
fi

echo "Pronto."
