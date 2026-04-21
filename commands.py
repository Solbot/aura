# commands.py
# Debug and utility commands for AURA.
# All commands start with "/" and are intercepted before being sent to the LLM.

import db
import memory as mem_module

def handle(user_input, system_prompt, assistant_name):
    """
    Check if input is a command. If so, handle it and return output string.
    Returns None if not a command (normal conversation).
    """
    s = user_input.strip()
    if not s.startswith("/"):
        return None

    parts = s.split()
    cmd   = parts[0].lower()
    args  = parts[1:] if len(parts) > 1 else []

    if cmd == "/help":
        return (
            f"\n--- {assistant_name} Debug Commands ---\n"
            "  /help              This list\n"
            "  /status            DB stats, hot count, dream state\n"
            "  /prompt            Show current system prompt\n"
            "  /hot               Show hot context messages\n"
            "  /memory            Show all stored profile facts\n"
            "  /warm              Show warm memory summaries\n"
            "  /cold [N]          Show last N cold archive entries (default 10)\n"
            "  /dream             Trigger dream cycle manually\n"
            "  /clear memory      Delete all profile facts\n"
            "  /clear warm        Delete all warm summaries\n"
            "  /clear hot         Clear hot context (keeps system prompt)\n"
            "  /set key value     Set a config value\n"
            "  /config            Show all config settings\n"
            "  /audio on|off      Toggle TTS audio output (persistent)\n"
            "  /tools             Toggle tool call debug output\n"
            "  /reboot            Restart all Aura services\n"
            "  /wipe              Wipe all data (requires: /wipe confirm)\n"
        )

    if cmd == "/status":
        import sqlite3, os
        conn  = sqlite3.connect(os.path.expanduser("~/aura/aura.db"))
        p_cnt = conn.execute("SELECT COUNT(*) FROM user_profile").fetchone()[0]
        w_cnt = conn.execute("SELECT COUNT(*) FROM conversation_summaries").fetchone()[0]
        c_cnt = conn.execute("SELECT COUNT(*) FROM conversation_archive").fetchone()[0]
        conn.close()
        hot   = mem_module.hot_count()
        pend  = db.get("dream_pending")
        delay = db.get("dream_delay")
        last  = db.get("last_interaction") or "never"
        return (
            f"\n--- Status ---\n"
            f"  Profile facts : {p_cnt}\n"
            f"  Warm summaries: {w_cnt}\n"
            f"  Cold messages : {c_cnt}\n"
            f"  Hot messages  : {hot}\n"
            f"  Dream pending : {pend}\n"
            f"  Dream delay   : {delay} min\n"
            f"  Last interact : {last[:19]}\n"
        )

    if cmd == "/prompt":
        return f"\n--- System Prompt ---\n{system_prompt}\n"

    if cmd == "/hot":
        hot = mem_module.get_hot()
        if not hot:
            return "\n[Hot memory is empty]\n"
        lines = [f"\n--- Hot Context ({len(hot)} messages) ---"]
        for m in hot:
            role    = m.get("role", "?")
            content = m.get("content") or "[tool call]"
            preview = str(content)[:120].replace("\n", " ")
            lines.append(f"  [{role}] {preview}")
        return "\n".join(lines) + "\n"

    if cmd == "/memory":
        facts = db.profile_get_all()
        if not facts:
            return "\n[No profile facts stored]\n"
        # Deduplicate — show most recent per key, dream facts starred
        seen = {}
        for f in facts:
            seen[f['key']] = f
        lines = [f"\n--- Profile Facts ({len(seen)} keys) ---"]
        for k, f in sorted(seen.items()):
            star = "★" if f['source'] == 'dream' else " "
            lines.append(f"  {star} {k}: {f['value']}  [{f['source']}]")
        lines.append("  (★ = dream-consolidated)")
        return "\n".join(lines) + "\n"

    if cmd == "/warm":
        warm = db.warm_get_all()
        if not warm:
            return "\n[No warm summaries]\n"
        lines = [f"\n--- Warm Summaries ({len(warm)}) ---"]
        for w in warm:
            lines.append(f"  [{w['created_at'][:16]}] {w['summary']}")
        return "\n".join(lines) + "\n"

    if cmd == "/cold":
        n = 10
        if args:
            try:
                n = int(args[0])
            except ValueError:
                pass
        import sqlite3, os
        conn = sqlite3.connect(os.path.expanduser("~/aura/aura.db"))
        rows = conn.execute(
            "SELECT role, content, timestamp FROM conversation_archive "
            "ORDER BY timestamp DESC LIMIT ?", (n,)
        ).fetchall()
        conn.close()
        if not rows:
            return "\n[Cold archive is empty]\n"
        lines = [f"\n--- Last {n} Cold Archive Entries ---"]
        for role, content, ts in reversed(rows):
            preview = str(content)[:100].replace("\n", " ")
            lines.append(f"  [{ts[:16]}] {role}: {preview}")
        return "\n".join(lines) + "\n"

    if cmd == "/dream":
        import dream, awareness
        if awareness._aether_busy:
            return f"\n[Cannot dream while {db.get('assistant_name')} is busy]\n"
        print(f"\n[Running dream cycle...]")
        result = dream.dream()
        if result:
            return f"\n[Dream complete: consolidated {len(result)} facts]\n"
        return "\n[Dream failed or nothing to consolidate]\n"

    if cmd == "/clear":
        if not args:
            return "\n[Usage: /clear memory | warm | hot]\n"
        target = args[0].lower()
        if target == "memory":
            import sqlite3, os
            conn = sqlite3.connect(os.path.expanduser("~/aura/aura.db"))
            conn.execute("DELETE FROM user_profile")
            conn.commit()
            conn.close()
            return "\n[Profile facts cleared]\n"
        if target == "warm":
            import sqlite3, os
            conn = sqlite3.connect(os.path.expanduser("~/aura/aura.db"))
            conn.execute("DELETE FROM conversation_summaries")
            conn.commit()
            conn.close()
            return "\n[Warm summaries cleared]\n"
        if target == "hot":
            mem_module.clear_hot()
            return "\n[Hot context cleared]\n"
        return f"\n[Unknown target: {target}. Use: memory | warm | hot]\n"

    if cmd == "/set":
        if len(args) < 2:
            return "\n[Usage: /set key value]\n"
        key   = args[0]
        value = " ".join(args[1:])
        db.set(key, value)
        return f"\n[Config set: {key} = {value}]\n"

    if cmd == "/config":
        import sqlite3, os
        conn = sqlite3.connect(os.path.expanduser("~/aura/aura.db"))
        rows = conn.execute(
            "SELECT key, value, description FROM config ORDER BY key"
        ).fetchall()
        conn.close()
        lines = ["\n--- Config ---"]
        for k, v, desc in rows:
            lines.append(f"  {k:<30} = {v}  ({desc})")
        return "\n".join(lines) + "\n"

    if cmd == "/audio":
        if not args:
            current = db.get('audio_enabled')
            state = "on" if current == '1' else "off"
            return f"\n[Audio is currently {state}. Use /audio on or /audio off]\n"
        setting = args[0].lower()
        if setting == "on":
            db.set('audio_enabled', '1')
            return "\n[Audio enabled]\n"
        elif setting == "off":
            db.set('audio_enabled', '0')
            return "\n[Audio disabled]\n"
        return "\n[Usage: /audio on | /audio off]\n"

    if cmd == "/tools":
        current = db.get("debug_tools")
        new = "0" if current == "1" else "1"
        db.set("debug_tools", new)
        state = "enabled" if new == "1" else "disabled"
        return f"\n[Tool debug {state}]\n"

    if cmd == "/reboot":
        def _do_reboot():
            import time, subprocess, os, glob, signal
            time.sleep(1.5)

            aura_dir = os.path.dirname(os.path.abspath(__file__))

            # ── Restart UI ──────────────────────────────────────────────────
            # Try systemd first; fall back to direct kill + spawn for
            # sessions started via start_ui.sh rather than the service.
            r = subprocess.run(
                ['sudo', 'systemctl', 'restart', 'aura-ui'], check=False)
            if r.returncode != 0:
                subprocess.run(['pkill', '-f', 'python.*aura_gtk'], check=False)
                time.sleep(0.5)

                uid = os.getuid()
                wayland_dir = f'/run/user/{uid}'
                socks = [s for s in glob.glob(f'{wayland_dir}/wayland-*')
                         if 'lock' not in s]
                env = dict(os.environ)
                if socks:
                    env['WAYLAND_DISPLAY'] = os.path.basename(socks[0])
                env['XDG_RUNTIME_DIR'] = wayland_dir
                env['PYTHONUNBUFFERED'] = '1'

                log_path = os.path.join(aura_dir, 'logs', 'ui.log')
                os.makedirs(os.path.dirname(log_path), exist_ok=True)
                with open(log_path, 'a') as lf:
                    subprocess.Popen(
                        ['python3', os.path.join(aura_dir, 'aura_gtk.py')],
                        cwd=aura_dir, env=env, stdout=lf, stderr=lf,
                    )

            # ── Restart backend ─────────────────────────────────────────────
            r = subprocess.run(
                ['sudo', 'systemctl', 'restart', 'aura'], check=False)
            if r.returncode != 0:
                # Not managed by systemd — SIGTERM self; start_ui.sh will
                # detect the exit and the user can restart the script.
                os.kill(os.getpid(), signal.SIGTERM)

        import threading
        threading.Thread(target=_do_reboot, daemon=True).start()
        return "\n[Rebooting — Aura services will restart momentarily.]\n"

    if cmd == "/wipe":
        if not args or args[0].lower() != "confirm":
            return (
                "\n--- /wipe WARNING ---\n"
                "  This permanently deletes ALL Aura data:\n"
                "    - All conversation history (hot / warm / cold)\n"
                "    - All learned profile facts and memory\n"
                "    - All config settings (reset to defaults)\n"
                "    - All reminders\n"
                "    - All log files (CSAM log is preserved)\n"
                "  The database will be rebuilt and first-boot setup\n"
                "  will run on the next start.\n\n"
                "  To confirm: /wipe confirm\n"
            )
        def _do_wipe():
            import time, subprocess, os, glob, signal
            time.sleep(1.5)
            aura_dir = os.path.dirname(os.path.abspath(__file__))
            # Remove database
            db_path = os.path.expanduser("~/aura/aura.db")
            try:
                os.remove(db_path)
            except Exception:
                pass
            # Remove log files — never touch the CSAM log
            log_dir = os.path.expanduser("~/aura/logs")
            for log_file in glob.glob(os.path.join(log_dir, "*.log")):
                try:
                    os.remove(log_file)
                except Exception:
                    pass
            # Restart UI + backend — same logic as /reboot
            r = subprocess.run(
                ['sudo', 'systemctl', 'restart', 'aura-ui'], check=False)
            if r.returncode != 0:
                subprocess.run(['pkill', '-f', 'python.*aura_gtk'], check=False)
                time.sleep(0.5)
                uid = os.getuid()
                wayland_dir = f'/run/user/{uid}'
                socks = [s for s in glob.glob(f'{wayland_dir}/wayland-*')
                         if 'lock' not in s]
                env = dict(os.environ)
                if socks:
                    env['WAYLAND_DISPLAY'] = os.path.basename(socks[0])
                env['XDG_RUNTIME_DIR'] = wayland_dir
                env['PYTHONUNBUFFERED'] = '1'
                log_path = os.path.join(aura_dir, 'logs', 'ui.log')
                os.makedirs(os.path.dirname(log_path), exist_ok=True)
                with open(log_path, 'a') as lf:
                    subprocess.Popen(
                        ['python3', os.path.join(aura_dir, 'aura_gtk.py')],
                        cwd=aura_dir, env=env, stdout=lf, stderr=lf,
                    )
            r = subprocess.run(
                ['sudo', 'systemctl', 'restart', 'aura'], check=False)
            if r.returncode != 0:
                os.kill(os.getpid(), signal.SIGTERM)
        import threading
        threading.Thread(target=_do_wipe, daemon=True).start()
        return (
            "\n[Wipe confirmed. All data is being erased.\n"
            " CSAM log preserved. Services restarting —\n"
            " first-boot setup will run on next launch.]\n"
        )

    return f"\n[Unknown command: {cmd}. Type /help for commands]\n"
