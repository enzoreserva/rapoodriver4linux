# -*- coding: utf-8 -*-
"""rapooctl - interface de linha de comando do rapoodriver4linux."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from .devices import registry
from .mouse import RapooMouse, MouseSettings
from .protocol import POLLING_RATE_LABELS, SENSOR_MODE_VALUES, DPI_STAGES
from .transport import enumerate_rapoo_endpoints, pick_config_endpoint


def _open_mouse(args) -> RapooMouse:
    conn = {"wired": 0xFF, "wireless": 0xA5}.get(getattr(args, "conn", None) or "")
    mouse = RapooMouse(connection=conn)
    mouse.open()
    return mouse


def _settings_dict(s: MouseSettings) -> dict:
    return {k: v for k, v in s.__dict__.items() if k != "raw_bytes"} | \
        {"raw": s.raw_bytes}


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
def cmd_devices(args) -> int:
    endpoints = enumerate_rapoo_endpoints()
    if not endpoints:
        print("Nenhum dispositivo Rapoo (VID 0x24AE) detectado.")
        print("Dica: instale a regra udev e reconecte o dongle/cabo.")
        return 1
    print("Dispositivos Rapoo detectados:")
    for ep in endpoints:
        model = registry.find_by_pid(ep.product_id)
        tag = model.model if model else "desconhecido"
        role = ""
        if model:
            if ep.product_id == model.wired_pid:
                role = " [mouse USB/cabo]"
            elif ep.product_id == model.dongle_pid:
                role = " [dongle 2.4GHz]"
        conf = " <- canal de configuração" if ep.vendor_page else ""
        print(f"  {ep.path}  PID 0x{ep.product_id:04X}  {tag}{role} "
              f"(iface {ep.interface_num}){conf}")
    return 0


def cmd_status(args) -> int:
    mouse = _open_mouse(args)
    try:
        st = mouse.read_status(timeout_s=args.timeout)
        if not st:
            print("Nenhum relatório de status recebido (relatório 7). O mouse pode estar "
                  "desligado, sem link RF ou fora de alcance. No Gen2 (VT3S MAX) o "
                  "status vem em rajadas irregulares (silêncios de minutos são normais); "
                  "aumente --timeout ou mexa o mouse e tente de novo.")
            return 2
        if args.json:
            print(json.dumps(st, ensure_ascii=False, indent=2))
        else:
            from .mouse import _format_status
            print(_format_status(st))
        return 0
    finally:
        mouse.close()


def cmd_get(args) -> int:
    mouse = _open_mouse(args)
    try:
        s = mouse.get_settings(profile=args.profile)
        if args.json:
            print(json.dumps(_settings_dict(s), ensure_ascii=False, indent=2))
        else:
            _print_settings(s)
        return 0
    finally:
        mouse.close()


def cmd_set(args) -> int:
    mouse = _open_mouse(args)
    try:
        p = args.profile
        changed = []
        if args.dpi is not None:
            mouse.set_dpi_stage_value(args.stage, dpi=args.dpi, profile=p)
            changed.append(f"DPI estágio {args.stage} X={args.dpi}")
        if args.dpi_y is not None:
            mouse.set_dpi_stage_value(args.stage, dpi_y=args.dpi_y, profile=p)
            changed.append(f"DPI estágio {args.stage} Y={args.dpi_y}")
        if args.dpi_stage is not None:
            mouse.set_dpi_stage(args.dpi_stage, profile=p)
            changed.append(f"estágio ativo -> {args.dpi_stage}")
        if args.dpi_count is not None:
            mouse.set_dpi_stage_count(args.dpi_count, profile=p)
            changed.append(f"contagem de estágios -> {args.dpi_count}")
        if args.polling is not None:
            mouse.set_polling_rate(args.polling, profile=p)
            changed.append(f"polling -> {args.polling}")
        if args.keyscan is not None:
            mouse.set_key_scan_rate(args.keyscan, profile=p)
            changed.append(f"key scan -> {args.keyscan}")
        if args.lod is not None:
            mouse.set_lod(args.lod, profile=p)
            changed.append(f"LOD -> {args.lod}")
        if args.motion_sync is not None:
            mouse.set_motion_sync(args.motion_sync, profile=p)
            changed.append(f"motion sync -> {args.motion_sync}")
        if args.glass is not None:
            mouse.set_glass_tracking(args.glass, profile=p)
            changed.append(f"glass tracking -> {args.glass}")
        if args.angle is not None:
            mouse.set_angle(args.angle, profile=p)
            changed.append(f"ângulo -> {args.angle}")
        if args.linear is not None or args.ripple is not None:
            mouse.set_corrections(linear=args.linear, ripple=args.ripple, profile=p)
            changed.append(f"correções -> linear={args.linear} ripple={args.ripple}")
        if args.debounce_press is not None or args.debounce_release is not None:
            mouse.set_debounce(args.debounce_press, args.debounce_release, profile=p)
            changed.append(f"debounce -> {args.debounce_press}/{args.debounce_release} ms")
        if args.sleep is not None:
            mouse.set_sleep(args.sleep, profile=p)
            changed.append(f"sleep -> {args.sleep} min")
        if args.sensor_mode is not None:
            mouse.set_sensor_mode(args.sensor_mode,
                                  polling_rate=args.sensor_mode_polling, profile=p)
            changed.append(f"preset -> {args.sensor_mode}")
        if args.rf_protocol is not None:
            mouse.set_rf_protocol(args.rf_protocol)
            changed.append(f"protocolo RF -> {args.rf_protocol}")
        if args.rf_boost is not None:
            mouse.set_rf_boost(args.rf_boost, profile=p)
            changed.append(f"RF boost -> {args.rf_boost}")
        if args.low_battery_light is not None:
            mouse.set_low_battery_light(args.low_battery_light, profile=p)
            changed.append(f"luz bateria baixa -> {args.low_battery_light}")
        if not changed:
            print("Nada para alterar: use uma das opções (--dpi, --polling, ...).")
            return 1
        for c in changed:
            print(f"  OK: {c}  (verificado por leitura)")
        s = mouse.get_settings(profile=p)
        if args.json:
            print(json.dumps(_settings_dict(s), ensure_ascii=False, indent=2))
        return 0
    finally:
        mouse.close()


def cmd_dpi(args) -> int:
    """Atalho direto para o caso de uso principal: mudar DPI."""
    mouse = _open_mouse(args)
    try:
        if args.value is None and args.y is not None:
            print("Erro: --y exige um valor de DPI (ex.: `rapooctl dpi 1200 --y 800`).",
                  file=sys.stderr)
            return 1
        p = args.profile
        if args.value is not None:
            mouse.set_dpi_stage_value(args.stage, dpi=args.value,
                                      dpi_y=args.y, profile=p)
            print(f"  OK: estágio {args.stage} = {args.value} DPI"
                  + (f" (Y={args.y})" if args.y else "")
                  + "  (verificado por leitura)")
        if args.active is not None:
            mouse.set_dpi_stage(args.active, profile=p)
            print(f"  OK: estágio ativo -> {args.active}")
        if args.count is not None:
            mouse.set_dpi_stage_count(args.count, profile=p)
            print(f"  OK: contagem de estágios -> {args.count}")
        if args.value is None and args.active is None and args.count is None:
            s = mouse.get_settings(profile=p)
            print(f"Estágio ativo: {s.dpi_stage} de {s.dpi_stage_count} estágios")
            for i, (x, y) in enumerate(zip(s.dpi_stages_x, s.dpi_stages_y)):
                mark = "*" if i == s.dpi_stage else " "
                print(f"  {mark} estágio {i}: {x:5d} x {y:5d} DPI")
            print(f"Limites do sensor ({mouse.model.sensor if mouse.model else '?'}): "
                  f"{mouse.dpi_range()[0]}-{mouse.dpi_range()[1]}")
        return 0
    finally:
        mouse.close()


def cmd_backup(args) -> int:
    mouse = _open_mouse(args)
    try:
        out = Path(args.output)
        payload = mouse.backup_profile(out, profile=args.profile)
        print(f"Backup completo do perfil {payload['profile']} salvo em {out} "
              f"(modelo {payload['model']}, base 0x{payload['base_address']:04X})")
        return 0
    finally:
        mouse.close()


def cmd_restore(args) -> int:
    mouse = _open_mouse(args)
    try:
        mouse.restore_profile(Path(args.input), profile=args.profile,
                              verify=not args.no_verify)
        print(f"Perfil {args.profile} restaurado de {args.input}"
              + ("" if args.no_verify else "  (verificado por leitura)"))
        return 0
    finally:
        mouse.close()


def cmd_raw(args) -> int:
    mouse = _open_mouse(args)
    try:
        # Addressing: offsets < 1024 are profile-relative (e.g. 640 = polling
        # rate); >= 1024 are absolute EEPROM addresses (e.g. 1536+... or 96).
        profile_rel = args.offset < 1024
        if args.op == "read":
            if profile_rel:
                data = mouse.read_memory(args.offset, args.length, args.profile)
            else:
                data = mouse.read_absolute_memory(args.offset, args.length)
            print(" ".join(f"{b:02X}" for b in data))
        elif args.op == "write":
            data = bytes.fromhex(args.data.replace(" ", ""))
            if not args.force:
                print("Escrita raw exige --force (pode danificar configurações).")
                return 1
            if profile_rel:
                mouse.write_memory(args.offset, data, args.profile)
            else:
                mouse.write_absolute_memory(args.offset, data)
            print(f"  OK: {len(data)} bytes gravados em offset {args.offset}"
                  + (" (perfil)" if profile_rel else " (absoluto)"))
        return 0
    finally:
        mouse.close()


def cmd_watch(args) -> int:
    mouse = _open_mouse(args)
    try:
        print("Ctrl+C para sair; imprime quando algo mudar (~3s heartbeat):")
        mouse.watch(poll_s=args.interval)
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        mouse.close()


def cmd_doctor(args) -> int:
    """Verificações de ponta a ponta - rode isto quando o mouse chegar."""
    ok = True

    def check(label: str, condition: bool, detail: str = "") -> None:
        nonlocal ok
        mark = "OK " if condition else "FALHOU"
        if not condition:
            ok = False
        print(f"  [{mark}] {label}" + (f" - {detail}" if detail else ""))

    print("rapooctl doctor - diagnóstico do ambiente:")
    import platform
    check("rodando no Linux", platform.system() == "Linux",
          platform.system())
    endpoints = enumerate_rapoo_endpoints()
    check("dispositivos Rapoo enumerados", bool(endpoints),
          f"{len(endpoints)} nó(s) hidraw")
    if endpoints:
        ep = pick_config_endpoint(endpoints)
        check("canal de configuração encontrado", ep is not None,
              f"{ep.path} (vendor_page={ep.vendor_page})" if ep else "")
        model = registry.find_by_pid(ep.product_id)
        check("modelo identificado no registry", model is not None,
              model.model if model else f"PID 0x{ep.product_id:04X} desconhecido")
        try:
            mouse = RapooMouse(endpoint=ep)
            mouse.open()
            try:
                check("calibração do payload", True,
                      f"offset={mouse.payload_offset}, conn="
                      + ("wired" if mouse.connection_type == 0xFF else "wireless"))
                s = mouse.get_settings(profile=0)
                check("leitura de configurações", True,
                      f"polling {s.polling_rate}, {s.dpi_stage_count} estágios, "
                      f"DPI ativo {s.dpi_stages_x[s.dpi_stage] if s.dpi_stage < len(s.dpi_stages_x) else '?'}")
                st = mouse.read_status(timeout_s=40)
                check("heartbeat (relatório 7)", st is not None,
                      f"bateria {st.get('battery_percent')}%" if st
                      else "sem heartbeat em 40s (mouse ligado/awake?)")
            finally:
                mouse.close()
        except Exception as exc:  # noqa: BLE001 - diagnóstico não deve quebrar
            check("comunicação com o dispositivo", False, str(exc))
    else:
        print("\nPróximos passos sugeridos:")
        print("  1. Conecte o dongle 2.4GHz (ou o mouse no cabo)")
        print("  2. Instale a regra udev: sudo ./install.sh")
        print("  3. Reconecte o dongle e rode novamente")
    return 0 if ok else 1


def cmd_models(args) -> int:
    models = registry.all_models()
    if args.query:
        models = registry.find(args.query)
    for m in models:
        print(f"  {m.model:<28} pids={[hex(p) for p in m.pids]}  "
              f"sensor={m.sensor or '?':<10} mcu={m.mcu or '?':<9} "
              f"bateria={m.battery or '?'}mAh")
    return 0


def _print_settings(s: MouseSettings) -> None:
    print(f"Perfil           : {s.profile}")
    print(f"DPI              : estágio ativo {s.dpi_stage} de {s.dpi_stage_count}")
    for i, (x, y) in enumerate(zip(s.dpi_stages_x, s.dpi_stages_y)):
        mark = "*" if i == s.dpi_stage else " "
        print(f"  {mark} estágio {i}: {x:5d} x {y:5d}")
    print(f"Polling rate     : {s.polling_rate}")
    print(f"Key scan rate    : {s.key_scan_rate}")
    print(f"LOD              : {s.lod_label} (índice {s.lod_index})")
    print(f"Motion sync      : {'sim' if s.motion_sync else 'não'}")
    print(f"Glass tracking   : {'sim' if s.glass_tracking else 'não'}")
    print(f"Correções        : linear={'sim' if s.linear_correction else 'não'} "
          f"ripple={'sim' if s.ripple_correction else 'não'}")
    print(f"Debounce         : press {s.debounce_press_ms} ms / release {s.debounce_release_ms} ms")
    print(f"Sleep            : {s.sleep_minutes} min")
    print(f"Ângulo sensor    : {s.sensor_angle} graus")
    print(f"Preset (atual)   : {s.sensor_mode_preset}")
    print(f"Protocolo RF     : {s.rf_protocol}")
    print(f"RF boost         : {s.rf_boost}")
    print(f"Luz bateria baixa: {'sim' if s.low_battery_light else 'não'}")


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------
def _add_conn_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--conn", choices=["wired", "wireless"], default=None,
                   help="força o tipo de conexão (auto por padrão)")
    p.add_argument("--profile", type=int, default=0, choices=range(0, 8),
                   help="perfil de configuração (0-7, padrão 0)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="rapooctl",
        description="rapoodriver4linux - driver Linux para mouses Rapoo "
                    "(VT3S MAX e família VT Gen2)",
        epilog="Protocolo documentado em docs/PROTOCOL.md. "
               "Créditos: projeto pedro3z0/rapoo-software-linux (MIT).")
    ap.add_argument("--version", action="version",
                    version="rapoodriver4linux 1.0.0")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("devices", help="lista os dispositivos Rapoo detectados")
    p.set_defaults(fn=cmd_devices)

    p = sub.add_parser("status", help="status ao vivo (bateria, DPI, polling, conexão)")
    p.add_argument("--timeout", type=float, default=30.0,
                   help="segundos aguardando o relatório de status (padrão 30; "
                        "no Gen2 silêncios de minutos são normais)")
    p.add_argument("--json", action="store_true", help="saída em JSON")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("get", help="mostra todas as configurações")
    _add_conn_arg(p)
    p.add_argument("--json", action="store_true", help="saída em JSON")
    p.set_defaults(fn=cmd_get)

    p = sub.add_parser("set", help="altera configurações (tudo verificado por leitura)")
    _add_conn_arg(p)
    p.add_argument("--dpi", type=int, help="DPI X do estágio escolhido (--stage)")
    p.add_argument("--dpi-y", type=int, dest="dpi_y",
                   help="DPI Y do estágio escolhido (opcional)")
    p.add_argument("--stage", type=int, default=0, choices=range(0, DPI_STAGES),
                   help="estágio alvo para --dpi/--dpi-y (0-6, padrão 0)")
    p.add_argument("--dpi-stage", type=int, choices=range(0, DPI_STAGES),
                   help="ativa o estágio DPI indicado")
    p.add_argument("--dpi-count", type=int, choices=range(1, DPI_STAGES + 1),
                   help="quantos estágios ficam habilitados (1-7)")
    p.add_argument("--polling", help="125Hz, 250Hz, 500Hz, 1000Hz, 2000Hz, 4000Hz, 8000Hz")
    p.add_argument("--keyscan", help="1000Hz..8000Hz (varredura dos botões)")
    p.add_argument("--lod", help="lift-off distance: mm da escala do modelo "
                                 "(ex.: 1.2) ou índice 1-11 (ex.: 5)")
    p.add_argument("--motion-sync", type=lambda v: v.lower() in ("1", "true", "sim", "yes", "on"),
                   metavar="true|false", help="motion sync on/off")
    p.add_argument("--glass", type=lambda v: v.lower() in ("1", "true", "sim", "yes", "on"),
                   metavar="true|false", help="glass tracking on/off")
    p.add_argument("--linear", type=lambda v: v.lower() in ("1", "true", "sim", "yes", "on"),
                   metavar="true|false", help="correção linear (angle snapping)")
    p.add_argument("--ripple", type=lambda v: v.lower() in ("1", "true", "sim", "yes", "on"),
                   metavar="true|false", help="correção ripple")
    p.add_argument("--angle", type=int, help="ângulo do sensor (-30 a +30)")
    p.add_argument("--debounce-press", type=int, help="debounce de aperto (0-32 ms)")
    p.add_argument("--debounce-release", type=int, help="debounce de soltar (0-32 ms)")
    p.add_argument("--sleep", type=int, help="timeout de sleep sem fio (1-60 min)")
    p.add_argument("--sensor-mode", help="office, balanced, firepower, hyperCore, competitive, frenzy")
    p.add_argument("--sensor-mode-polling", dest="sensor_mode_polling",
                   help="slot de polling alvo do preset (padrão: taxa atual)")
    p.add_argument("--rf-protocol", choices=["turbo", "initial"],
                   help="protocolo de comunicação global")
    p.add_argument("--rf-boost", choices=["adaptive", "maximum"],
                   help="estratégia de potência RF")
    p.add_argument("--low-battery-light", type=lambda v: v.lower() in ("1", "true", "sim", "yes", "on"),
                   metavar="true|false", help="alerta LED de bateria baixa")
    p.add_argument("--json", action="store_true", help="mostra snapshot final em JSON")
    p.set_defaults(fn=cmd_set)

    p = sub.add_parser("dpi", help="atalho para leitura/escrita de DPI")
    _add_conn_arg(p)
    p.add_argument("value", nargs="?", type=int, default=None,
                   help="novo DPI (omite para apenas mostrar)")
    p.add_argument("--y", type=int, default=None,
                   help="DPI Y independente do estágio (sem --y, Y não é alterado)")
    p.add_argument("--stage", type=int, default=0, choices=range(0, DPI_STAGES),
                   help="estágio a configurar (0-6, padrão 0)")
    p.add_argument("--active", type=int, choices=range(0, DPI_STAGES),
                   help="ativa o estágio indicado")
    p.add_argument("--count", type=int, choices=range(1, DPI_STAGES + 1),
                   help="número de estágios habilitados (1-7)")
    p.set_defaults(fn=cmd_dpi)

    p = sub.add_parser("backup", help="backup completo (1024 bytes brutos do perfil)")
    _add_conn_arg(p)
    p.add_argument("output", help="arquivo de saída (.json)")
    p.set_defaults(fn=cmd_backup)

    p = sub.add_parser("restore", help="restaura um backup completo")
    _add_conn_arg(p)
    p.add_argument("input", help="arquivo de backup (.json)")
    p.add_argument("--no-verify", action="store_true",
                   help="não relê para confirmar (não recomendado)")
    p.set_defaults(fn=cmd_restore)

    p = sub.add_parser("raw", help="acesso cru à memória (avançado)")
    _add_conn_arg(p)
    rawsub = p.add_subparsers(dest="op", required=True)
    pr = rawsub.add_parser("read", help="lê bytes (hex)")
    pr.add_argument("--offset", type=int, required=True,
                    help="endereço absoluto (>=1024) ou offset de perfil com --profile")
    pr.add_argument("--length", type=int, default=8)
    pr.set_defaults(fn=cmd_raw)
    pw = rawsub.add_parser("write", help="grava bytes (hex) - exige --force")
    pw.add_argument("--offset", type=int, required=True)
    pw.add_argument("--data", required=True, help="bytes em hex (ex.: 01 a0 88 13)")
    pw.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_raw)

    p = sub.add_parser("watch", help="acompanha o status ao vivo")
    _add_conn_arg(p)
    p.add_argument("--interval", type=float, default=1.0)
    p.set_defaults(fn=cmd_watch)

    p = sub.add_parser("doctor", help="diagnóstico do ambiente e do dispositivo")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("models", help="lista os modelos suportados")
    p.add_argument("query", nargs="?", default=None, help="filtro por nome (ex.: VT3S)")
    p.set_defaults(fn=cmd_models)

    return ap


def main(argv: Optional[list] = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except TimeoutError as exc:
        print(f"Erro: tempo esgotado conversando com o mouse ({exc})", file=sys.stderr)
        print("Dicas: reconecte o dongle, confira a regra udev e rode `rapooctl doctor`.",
              file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"Erro: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
