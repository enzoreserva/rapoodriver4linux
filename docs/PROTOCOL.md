# Rapoo Gaming Mouse HID Protocol — rapoodriver4linux

Especificação do protocolo HID usado pelos mouses Rapoo da família VT
(Gen2, MCU Nordic nRF54L, ex.: **VT3S MAX**, VT3 Air, VT3S Air, VT9 Pro V2...).

Documentação derivada de:
- Engenharia reversa do driver oficial **"A HUB" v1.0.19** (Windows/macOS) e
  do **Web Driver oficial** (hub.rapoo.cn / hub.rapoo.com) — projeto
  [pedro3z0/rapoo-software-linux](https://github.com/pedro3z0/rapoo-software-linux) (MIT);
- Capturas em hardware real pela comunidade (VT0 Air MAX, link 2.4G e cabo);
- Análise dos instaladores oficiais `A HUB_Game_Win/MAC_V1.0.19_20260713`.

---

## 1. Camada física/HID

Os dispositivos (e o dongle 2.4GHz) expõem múltiplas interfaces HID:

- **Interface 0 (mouse de boot)**: relatórios de entrada padrão de 8 bytes
  (movimentação do cursor).
- **Interface 1 (controle/custom)** — o canal de configuração:
  - **Report ID 6** — *Output Report*, 32 bytes (host → dispositivo: comandos)
  - **Report ID 8** — *Feature Report*, 32 bytes (dispositivo → host: respostas)
  - **Report ID 7** — *Input Report*, heartbeat de status/bateria (~3 s)
  - Usage Page `0xFF00` (vendor-defined), Usage `0x02` (ou `0x0E`)
- **Interface 2**: frames vendor de 512 bytes (OTA de firmware — evitar).

No Linux cada interface vira um nó `/dev/hidraw*`. O `rapooctl` identifica
o canal correto lendo o `report_descriptor` de cada nó (busca por Usage Page
`0xFF00`) e caindo para a interface 1 quando o descritor não está acessível.

## 2. Arquitetura de memória

Configurações vivem em uma "EEPROM virtual" de até 8 perfis de 1024 bytes:

```
Perfil 0: base 1536 (0x0600)
Perfil 1: base 2560 (0x0A00)   ... passo de +1024 bytes por perfil
Perfil 7: base 8704 (0x2200)
```

### Mapa de memória (offsets relativos à base do perfil)

| Offset | Tamanho | Campo | Descrição |
|---|---|---|---|
| 0–27 | 7×4 | botões | Mapeamento: LEFT/MID/RIGHT/CPIADD/CPIREDUCE/FORWARD/BACK |
| 640 | 1 | `MOUSE_REPORT` | Código da taxa de polling (tabela abaixo) |
| 641 | 1 | `MOUSE_SCAN_RATE` | Índice da taxa de varredura dos botões |
| 644 | 1 | `MOUSE_SLIGHT` | Lift-off distance (índice 1-based na escala do modelo) |
| 645 | 1 | `MOUSE_MOTION` | Motion Sync (0/1) |
| 648 | 14 | `MOUSE_DPI_X_LIST` | 7 estágios × u16 LE (DPI eixo X) |
| 662 | 1 | `MOUSE_DPI_ENABLE_GEAR` | Quantidade de estágios habilitados − 1 |
| 664 | 1 | `MOUSE_DPI_CUR` | Estágio ativo (0–6) |
| 704 | 1 | `MOUSE_DOWNDELAY` | Debounce de aperto (ms, 0–32) |
| 705 | 1 | `MOUSE_LIFTDELAY` | Debounce de soltar (ms, 0–32) |
| 706 | 1 | `MOUSE_SLEEPTIME` | Timeout de sleep sem fio (minutos) |
| 707 | 1 | — | bit0 correção linear, bit1 correção ripple |
| 708 | 1 | `MOUSE_SENSORANGLE` | Ângulo do sensor (s8, −30° a +30°) |
| 709 | 1 | `MOUSE_GLASS` | Glass tracking (0/1) |
| 712 | 14 | `MOUSE_DPI_Y_LIST` | 7 estágios × u16 LE (DPI eixo Y) |
| 728 | 1 | RF strategy | 0=Adaptive, 1=Maximum RF |
| 729 | 1 | Low-battery light | LED de bateria baixa (0/1) |
| 732 | 7 | Presets de desempenho | Um preset por slot de polling (125→8000 Hz) |

Espaço **global** (sem base de perfil): offset **96** = protocolo de
comunicação (0=turbo, 2=initial).

**DPI não tem comando dedicado** — é gravado por escritas EEPROM `0xA5`
nos endereços 648/712 (u16 LE) + contagem/estágio ativo em 662/664.

## 3. Estrutura dos pacotes

Comandos via Output Report 6 (32 bytes + 1 byte de report ID no hidraw):

### Leitura (`0xA4`)
```
Byte 0: tipo de conexão (0xA5 = 2.4G wireless, 0xFF = USB cabo)
Byte 1: 0xA4
Byte 2: comprimento a ler
Bytes 3–6: endereço (LE)
Bytes 7–31: 0x00
```

### Escrita (`0xA5`)
```
Byte 0: tipo de conexão (0xA5 / 0xFF)
Byte 1: 0xA5
Byte 2: comprimento do payload (máx. 25)
Bytes 3–6: endereço (LE)
Bytes 7..7+N-1: dados
Resto: 0x00
```

### Resposta (Feature Report 8)

Dois layouts, dependendo do kernel:

```
Layout A (report ID removido — visão do WebHID oficial):
Byte 0:   ACK (0x01=OK, 0x02=busy/retry)
Bytes 1–3: eco (cmd/endereço)
Bytes 4..: dados

Layout B (report ID presente — típico do HIDIOCGFEATURE Linux):
Byte 0:   0x08
Byte 1:   ACK
Bytes 2–4: eco
Bytes 5..: dados
```

O `rapooctl` **auto-calibra** o offset do payload (5/4/6) na primeira
leitura, validando registros de valor conhecido (640, 641, 644, 645, 664).
Override manual: `RAPOO_DATA_OFFSET=4|5|6` ou `--conn` para o tipo de
conexão.

## 4. Códigos de polling rate (offset 640)

| Código | Taxa |
|---|---|
| `8` | 125 Hz |
| `4` | 250 Hz |
| `2` | 500 Hz |
| `1` | 1000 Hz |
| `132` (0x84) | 2000 Hz |
| `130` (0x82) | 4000 Hz |
| `129` (0x81) | 8000 Hz |

## 5. Outras tabelas

**Key scan rate** (offset 641): índice `3`=1000 Hz (padrão), `4`=2000 Hz,
`5`=4000 Hz, `6`=8000 Hz (apenas 3–6 em dispositivos retail).

**Presets de desempenho** (perfil+732, 7 slots — ordem 125→8000 Hz):
`0`=office, `1`=balanced, `2`=firepower, `3`=hyperCore, `4`=competitive,
`5`=frenzy. Valores legais por slot (tabela oficial "VV"):

| Slot | Permitidos |
|---|---|
| 125/250 Hz | 0–1 (office/balanced) |
| 500 Hz | 0–2 |
| 1000 Hz | 1–5 |
| 2000 Hz | 2–5 |
| 4000/8000 Hz | 3–5 |

**LOD** (offset 644): índice 1-based. Escala LV (PAW3950U/3955U — VT3S MAX):
0,7–1,7 mm; escala MV (PAW3398 legado): 1,0–2,0 mm.

**Limites de DPI por sensor**: PAW3950U/PAW3955U → 10–45000 (VT3S MAX);
PAW3398 → 50–26000.

## 6. Status ao vivo (Input Report 7 — visão hidraw)

| Byte | Significado |
|---|---|
| 0 | report id `0x07` |
| 1 | low nibble conexão (`0`=wireless 2.4G, `2`=cabo) / high nibble classe do sensor |
| 2 | estágio DPI ativo |
| 3–4 | DPI X (u16 LE) |
| 5–6 | DPI Y (u16 LE) |
| 7 | flag de validade da bateria (`0` = nível válido) |
| 8 | bateria % (0–100) |
| 9 | backlight |
| 10 | índice key scan |
| 11 | código de polling ativo |
| 12 | perfil ativo |
| 13–14 | USB PID (u16 LE) |
| 18 | bit7 = RF conectado, bits0–6 = RSSI |

## 7. Robustez implementada no rapooctl

- Auto-calibração do layout de resposta (A/B) por sondagem de registros.
- Retry em ACK `0x02` (busy) e timeout configurável.
- **Verificação pós-escrita**: toda gravação é relida e comparada; divergência
  retenta antes de reportar erro — configuração nunca falha silenciosamente.
- Transferências grandes (backup de 1024 bytes) são fatiadas em frames de 25 bytes.
- Identificação do canal de configuração pelo descritor HID (Usage Page 0xFF00).

## 8. Pendências / roadmap

- Remapeamento de botões e macros (endereços 0–52 e `MACRO_*` 64768+/65536+)
- Troca de perfil por software (comando não confirmado em hardware)
- RGB (`MOUSE_LIGHTMOD` 665 / `MOUSE_LIGHTRGB` 696) onde presente
- Pareamento do dongle (handshake no receptor — maior risco)
- Mapping nibble de sensor → modelo PixArt (precisa de 1 captura ao vivo)
