# rapoodriver4linux

Driver Linux para o mouse **Rapoo VT3S MAX** (e toda a família VT Gen2).
Feito porque o VT3S MAX **não tem botões físicos de DPI** e o software
oficial da Rapoo ("A HUB") só existe para Windows/macOS.

**Puro Python 3, zero dependências** — só precisa da regra udev instalada.

```
rapidíssimo: mude o DPI agora
┌──────────────────────────────────────────────────────────┐
│ git clone https://github.com/enzoreserva/rapoodriver4linux│
│ cd rapoodriver4linux && ./install.sh                      │
│ rapooctl dpi                    # mostra os estágios      │
│ rapooctl dpi 3200 --stage 0     # muda o estágio 0 p/ 3200│
└──────────────────────────────────────────────────────────┘
```

---

## O que funciona

| Recurso | Comando |
|---|---|
| DPI (7 estágios, X/Y independentes, estágio ativo, nº de estágios) | `dpi`, `set --dpi` |
| Polling rate (125 a 8000 Hz) | `set --polling 8000Hz` |
| Bateria / status ao vivo | `status`, `watch` |
| Lift-off distance | `set --lod 1.0` |
| Motion sync, glass tracking, ângulo do sensor | `set --motion-sync true` etc. |
| Debounce dos cliques (0–32 ms) | `set --debounce-press 2` |
| Presets de desempenho (office…frenzy) | `set --sensor-mode hyperCore` |
| Sleep sem fio | `set --sleep 15` |
| Potência RF, protocolo, LED bateria baixa | `set --rf-boost maximum` etc. |
| Backup/restauração completa do perfil (1024 bytes brutos) | `backup`, `restore` |
| Acesso cru à memória (avançado) | `raw` |

Todas as **escritas são verificadas por releitura** (retry automático em
caso de divergência) — configuração nunca falha silenciosamente.

Toda configuração é gravada na **memória interna do mouse** (onboard):
vale em qualquer sistema, com ou sem o driver instalado.

## Dispositivos suportados

O protocolo cobre a família nRF54L do driver oficial — 102 modelos no
registry (`rapooctl models`). O alvo principal e os mais testados:

| Modelo | Dongle (2.4G) | Cabo (USB) |
|---|---|---|
| **VT3S MAX** (49g Gen2, PAW3950U, 800mAh) | `24AE:1421` | `24AE:4621` |
| VT3S MAX V2 | `24AE:1B07` | `24AE:4B07` |
| VT3 Air / VT0 Air MAX | `24AE:14A1` | `24AE:46A1` |
| VT3S Air Max | `24AE:14A2` | `24AE:46A2` |
| VT3 PRO V2 / VT3S PRO V2 | `24AE:14A5/14A6` | `24AE:46A5/46A6` |
| VT3 MAX | `24AE:1417` | `24AE:4617` |
| VT9 PRO V2 / VT7 PRO V2 | `24AE:14A9/14A7` | `24AE:46A9/46A7` |
| VT0 / VT0 MAX | `24AE:1410/1420` | `24AE:4610/4620` |

\* PIDs completos na tabela interna: `rapooctl models VT3S`.

Se o seu PID não estiver no registry o driver ainda tenta falar com o
dispositivo (detecção automática cabo/dongle + limites de DPI genéricos).

## Instalação (Fedora KDE — funciona em qualquer distro com udev)

```bash
git clone https://github.com/enzoreserva/rapoodriver4linux
cd rapoodriver4linux

./install.sh          # regra udev + comando rapooctl em ~/.local/bin
```

A `install.sh` faz apenas duas coisas:

1. Copia `udev/99-rapoo.rules` para `/etc/udev/rules.d/` (o único passo
   que usa `sudo`) e recarrega as regras.
2. Cria o symlink do `rapooctl` em `~/.local/bin`.

Depois **desconecte e reconecte o dongle 2.4GHz** (ou o cabo) para a regra
valer. Requisito: `python3` ≥ 3.9 (o Fedora já vem com). Nada de pip/venv.

<details>
<summary>Instalação manual (sem script)</summary>

```bash
echo 'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="24ae", MODE="0666", TAG+="uaccess"' \
  | sudo tee /etc/udev/rules.d/99-rapoo.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
sudo cp rapooctl /usr/local/bin/   # opcional
```
</details>

## Uso

```bash
rapooctl devices                # lista mouses/dongles Rapoo detectados
rapooctl doctor                 # diagnóstico completo do ambiente
rapooctl status                 # bateria, DPI, polling, conexão (~3s heartbeat)
rapooctl get                    # todas as configurações

# ---- DPI (o motivo deste projeto existir) ----
rapooctl dpi                    # mostra os 7 estágios e o ativo
rapooctl dpi 3200               # estágio 0 = 3200 DPI (padrão do sensor: 10-45000)
rapooctl dpi 1600 --stage 1     # estágio 1 = 1600
rapooctl dpi 3200 --active 1    # ativa o estágio 1
rapooctl dpi --count 3          # só 3 estágios no ciclo
rapooctl dpi 1200 --y 800       # X=1200, Y=800 (sniper)

# ---- polling / sensor ----
rapooctl set --polling 8000Hz
rapooctl set --keyscan 4000Hz
rapooctl set --sensor-mode competitive --polling 8000Hz
rapooctl set --lod 1.0 --motion-sync true --debounce-press 2
rapooctl set --angle 0 --glass true

# ---- perfis / backup ----
rapooctl backup meu-perfil.json            # dump bruto de 1024 bytes
rapooctl restore meu-perfil.json           # restaura (verificado)
rapooctl raw read --offset 640 --length 8  # diagnóstico de memória

# ---- ao vivo ----
rapooctl watch                  # imprime quando algo mudar (bateria, etc.)
```

Saída JSON para scripts: `--json` em `get`/`status`.

## Quando o mouse chegar — checklist

O driver foi construído **sem acesso ao hardware** (mouse ainda em trânsito
e máquina atual é Windows), validado por 26 testes unitários contra um
dispositivo simulado + protocolo confirmado em hardware pela comunidade.
No primeiro contato real, faça na ordem:

1. `./install.sh` (se ainda não rodou) e reconecte o dongle.
2. `rapooctl doctor` — verifica enumeração, identificação do modelo
   (VT3S MAX = PID `24AE:1421/4621`), calibração do protocolo e heartbeat.
3. `rapooctl get` — snapshot atual (antes de mudar qualquer coisa).
4. `rapooctl backup factory.json` — **backup de fábrica**. Guarde este arquivo!
5. Só então: `rapooctl dpi 3200 --stage 0`, `rapooctl set --polling 1000Hz`, etc.
6. Valide no sistema: movimentos do cursor refletem o DPI/polling novos.

Se algo estranho ocorrer: `RAPOO_DATA_OFFSET=4|5|6 rapooctl raw read
--offset 640 --length 8` e `rapooctl doctor` coletam os dados para
depuração. O backup da etapa 4 restaura qualquer estado.

## Alternativa oficial (GUI, também funciona no Linux)

A Rapoo tem um **Web Driver oficial** que roda no navegador via WebHID:
[hub.rapoo.com](https://hub.rapoo.com/) (Chrome/Chromium/Brave/Edge;
Firefox não suporta WebHID). No Linux ele precisa **da mesma regra udev**
que este projeto instala — depois dela o site funciona normalmente.
O `rapooctl` cobre a mesma configuração por linha de comando/scripts.

## Segurança e limitações

- `backup` antes de experimentar; `restore` desfaz qualquer alteração.
- `raw write` exige `--force` (endereços errados podem bagunçar a config —
  use apenas seguindo `docs/PROTOCOL.md`).
- **Não implementado ainda** (roadmap em `docs/PROTOCOL.md`): remapeamento
  de botões/macros, RGB, troca de perfil via software, pareamento do dongle.
- Firmware/OTA: fora de escopo por segurança (não mexemos no bootloader).

## Estrutura do projeto

```
rapooctl              # launcher do CLI
rapoo/
  protocol.py         # constantes, framing 0xA4/0xA5, codecs (testado)
  transport.py        # hidraw puro-stdlib (ioctl HIDIOCGFEATURE), enumeração sysfs
  devices.py          # registry com 102 modelos do web driver oficial
  mouse.py            # lógica de alto nível: calibração, escrita verificada, backup
  cli.py              # rapooctl (pt-BR)
udev/99-rapoo.rules   # acesso sem privilégio aos hidraw (também libera o web driver)
tests/test_protocol.py# 26 testes sem hardware (dispositivo simulado)
docs/PROTOCOL.md      # especificação completa do protocolo
data/rapoo_models.json# tabela de dispositivos do web driver oficial
install.sh / uninstall.sh
```

## Referências e créditos

- [pedro3z0/rapoo-software-linux](https://github.com/pedro3z0/rapoo-software-linux) (MIT)
  — engenharia reversa do protocolo (A HUB v1.0.19 + web driver) e capturas
  em hardware real. A base do conhecimento aqui vem desse projeto.
- Driver oficial analisado: `A HUB v1.0.19` (Win/Mac, 2026-07-13) — Qt/QML
  + `libRapooDevice` (hidapi). Os instaladores não estão no repo por
  tamanho (~660 MB): baixe em [shop.rapoo.com](https://shop.rapoo.com/).
- [Web Driver oficial](https://hub.rapoo.com/) (WebHID).

Licença MIT (veja `LICENSE`).
