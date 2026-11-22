#!/usr/bin/env bash
# rapoodriver4linux - instalador
#
# Faz duas coisas, nada mais:
#   1. Regra udev -> permite acesso sem privilegio aos /dev/hidraw* da Rapoo
#      (necessario tanto para o rapooctl quanto para o web driver oficial
#      hub.rapoo.com via WebHID).
#   2. Comando `rapooctl` -> symlink em ~/.local/bin (Python 3 puro,
#      sem pip, sem venv, sem dependencias).
#
# Uso:
#   ./install.sh           # instala regra udev + comando rapooctl
#   ./install.sh --dev     # + ambiente pytest para rodar os testes
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

echo "=== rapoodriver4linux - instalação ==="

# 0. Python 3
if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 não encontrado. No Fedora:" >&2
    echo "  sudo dnf install python3" >&2
    exit 1
fi
if ! python3 -c 'import sys; assert sys.version_info >= (3, 9)' 2>/dev/null; then
    echo "Python 3.9+ é necessário." >&2
    exit 1
fi

# 1. Regra udev (única etapa que precisa de root)
UDEV_DST="/etc/udev/rules.d/99-rapoo.rules"
if [[ -d "/etc/udev/rules.d" ]]; then
    if [[ -f "$UDEV_DST" ]] && cmp -s "$HERE/udev/99-rapoo.rules" "$UDEV_DST"; then
        echo "[1/2] regra udev já instalada e atualizada."
    else
        echo "[1/2] instalando regra udev (sudo)..."
        if sudo cp "$HERE/udev/99-rapoo.rules" "$UDEV_DST" \
            && sudo udevadm control --reload-rules \
            && sudo udevadm trigger; then
            echo "      ok. Desconecte e reconecte o dongle 2.4GHz (ou o cabo) para aplicar."
        else
            echo "      não consegui instalar automaticamente. Rode manualmente:" >&2
            echo "        sudo cp $HERE/udev/99-rapoo.rules /etc/udev/rules.d/" >&2
            echo "        sudo udevadm control --reload-rules && sudo udevadm trigger" >&2
        fi
    fi
else
    echo "[1/2] /etc/udev/rules.d não existe (distro sem systemd-udev?). Regra não instalada." >&2
fi

# 2. Comando rapooctl (não precisa de root)
echo "[2/2] instalando comando 'rapooctl' em ~/.local/bin ..."
mkdir -p "$HOME/.local/bin"
chmod +x "$HERE/rapooctl"
ln -sfn "$HERE/rapooctl" "$HOME/.local/bin/rapooctl"
case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) echo "      NOTA: adicione ~/.local/bin ao PATH (ex.: em ~/.bashrc):"
       echo '      export PATH="$HOME/.local/bin:$PATH"' ;;
esac

# Extras de desenvolvimento
if [[ "${1:-}" == "--dev" ]]; then
    echo "[dev] criando ambiente com pytest..."
    if command -v uv >/dev/null 2>&1; then
        uv venv "$HERE/.venv" --quiet
        uv pip install --python "$HERE/.venv/bin/python" pytest --quiet
    else
        python3 -m venv "$HERE/.venv"
        "$HERE/.venv/bin/pip" install -q pytest
    fi
    echo "      testes: .venv/bin/python -m pytest tests/"
fi

echo ""
echo "=== Instalação concluída! ==="
echo ""
echo "Teste agora (com o mouse/dongle conectado):"
echo "  rapooctl devices      # lista os dispositivos Rapoo"
echo "  rapooctl doctor       # diagnóstico completo"
echo "  rapooctl dpi          # mostra os estágios de DPI"
echo "  rapooctl dpi 3200 --stage 0   # exemplo: DPI 3200 no estágio 0"
