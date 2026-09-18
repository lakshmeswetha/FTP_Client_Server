import socket
import os
import sys

# ─── Configuration ────────────────────────────────────────────────────────────
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9000
BUFFER_SIZE = 4096
DOWNLOAD_DIR = "downloads"


# ─── Helper Functions ──────────────────────────────────────────────────────────

def send_command(sock, command):
    """Send a command string to the server."""
    sock.sendall((command + "\n").encode())


def read_line(sock):
    """Read one line from the server (terminated by \\n)."""
    data = b""
    while not data.endswith(b"\n"):
        chunk = sock.recv(1)
        if not chunk:
            raise ConnectionError("Server closed the connection.")
        data += chunk
    return data.decode().strip()


def recv_exact(sock, num_bytes, file_handle, progress=True):
    """Receive exactly num_bytes from socket and write to file."""
    received = 0
    while received < num_bytes:
        remaining = num_bytes - received
        chunk = sock.recv(min(BUFFER_SIZE, remaining))
        if not chunk:
            raise ConnectionError("Connection lost during transfer.")
        file_handle.write(chunk)
        received += len(chunk)
        if progress:
            percent = int((received / num_bytes) * 100)
            bar = "█" * (percent // 5) + "░" * (20 - percent // 5)
            print(f"\r  [{bar}] {percent}% ({received}/{num_bytes} bytes)", end="", flush=True)
    if progress:
        print()
    return received


# ─── Command Implementations ───────────────────────────────────────────────────

def cmd_list(sock):
    """Request and display the file list from the server."""
    send_command(sock, "LIST")
    response = read_line(sock)
    parts = response.split()

    if not parts[0] == "200":
        print(f"  ✗ Server error: {response}")
        return

    count = int(parts[2])
    if count == 0:
        print("  (No files available on server)")
        return

    print(f"\n  {'Filename':<30} {'Size':>12}")
    print(f"  {'-'*30} {'-'*12}")
    for _ in range(count):
        line = read_line(sock)
        name, size = line.rsplit(" ", 1)
        size_kb = f"{int(size):,} B"
        print(f"  {name:<30} {size_kb:>12}")
    print()


def cmd_get(sock, filename):
    """Download a file from the server."""
    send_command(sock, f"GET {filename}")
    response = read_line(sock)
    parts = response.split()

    if parts[0] != "200":
        code_map = {
            "404": "File not found on server.",
            "403": "Permission denied.",
            "500": "Server error."
        }
        reason = code_map.get(parts[0], response)
        print(f"  ✗ {reason}")
        return

    file_size = int(parts[2])
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    save_path = os.path.join(DOWNLOAD_DIR, os.path.basename(filename))

    print(f"  Downloading '{filename}' ({file_size:,} bytes)...")
    try:
        with open(save_path, "wb") as f:
            recv_exact(sock, file_size, f)
        print(f"  ✓ Saved to '{save_path}'")
    except ConnectionError as e:
        print(f"  ✗ Transfer failed: {e}")
    except OSError as e:
        print(f"  ✗ Could not save file: {e}")


def cmd_put(sock, local_path):
    """Upload a local file to the server."""
    if not os.path.exists(local_path):
        print(f"  ✗ Local file not found: '{local_path}'")
        return

    if not os.access(local_path, os.R_OK):
        print(f"  ✗ Permission denied reading: '{local_path}'")
        return

    filename = os.path.basename(local_path)
    file_size = os.path.getsize(local_path)

    send_command(sock, f"PUT {filename} {file_size}")
    response = read_line(sock)

    if not response.startswith("200 READY"):
        print(f"  ✗ Server rejected upload: {response}")
        return

    print(f"  Uploading '{filename}' ({file_size:,} bytes)...")
    try:
        sent = 0
        with open(local_path, "rb") as f:
            while chunk := f.read(BUFFER_SIZE):
                sock.sendall(chunk)
                sent += len(chunk)
                percent = int((sent / file_size) * 100) if file_size > 0 else 100
                bar = "█" * (percent // 5) + "░" * (20 - percent // 5)
                print(f"\r  [{bar}] {percent}% ({sent}/{file_size} bytes)", end="", flush=True)
        print()

        confirm = read_line(sock)
        if confirm.startswith("200 OK"):
            print(f"  ✓ Upload complete.")
        else:
            print(f"  ✗ Server reported: {confirm}")
    except (ConnectionError, BrokenPipeError) as e:
        print(f"  ✗ Upload failed: {e}")


def cmd_delete(sock, filename):
    """Request server to delete a file."""
    confirm = input(f"  Are you sure you want to delete '{filename}' from server? (y/n): ").strip()
    if confirm.lower() != "y":
        print("  Cancelled.")
        return

    send_command(sock, f"DELETE {filename}")
    response = read_line(sock)
    parts = response.split()

    if parts[0] == "200":
        print(f"  ✓ '{filename}' deleted from server.")
    elif parts[0] == "404":
        print(f"  ✗ File not found on server.")
    elif parts[0] == "403":
        print(f"  ✗ Permission denied.")
    else:
        print(f"  ✗ Server error: {response}")


# ─── Interactive Shell ─────────────────────────────────────────────────────────

HELP_TEXT = """
  Available Commands:
  ─────────────────────────────────────────
  LIST              List all files on server
  GET <filename>    Download a file
  PUT <filepath>    Upload a local file
  DELETE <filename> Delete a file on server
  HELP              Show this help message
  QUIT              Disconnect and exit
  ─────────────────────────────────────────
"""

BANNER = """
╔══════════════════════════════════════╗
║       FTP-Like Client v1.0           ║
║  Type HELP for available commands    ║
╚══════════════════════════════════════╝
"""

def run_client(host, port):
    print(BANNER)
    print(f"  Connecting to {host}:{port}...")

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((host, port))
    except ConnectionRefusedError:
        print(f"  ✗ Could not connect to {host}:{port}. Is the server running?")
        sys.exit(1)
    except Exception as e:
        print(f"  ✗ Connection error: {e}")
        sys.exit(1)

    greeting = read_line(sock)
    print(f"  Server: {greeting}\n")

    try:
        while True:
            try:
                raw = input("ftp> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n  Disconnecting...")
                send_command(sock, "QUIT")
                break

            if not raw:
                continue

            parts = raw.split()
            cmd = parts[0].upper()

            if cmd == "LIST":
                cmd_list(sock)
            elif cmd == "GET":
                if len(parts) < 2:
                    print("  Usage: GET <filename>")
                else:
                    cmd_get(sock, parts[1])
            elif cmd == "PUT":
                if len(parts) < 2:
                    print("  Usage: PUT <local_filepath>")
                else:
                    cmd_put(sock, parts[1])
            elif cmd == "DELETE":
                if len(parts) < 2:
                    print("  Usage: DELETE <filename>")
                else:
                    cmd_delete(sock, parts[1])
            elif cmd == "HELP":
                print(HELP_TEXT)
            elif cmd == "QUIT":
                send_command(sock, "QUIT")
                response = read_line(sock)
                print(f"  Server: {response}")
                break
            else:
                print(f"  ✗ Unknown command '{cmd}'. Type HELP for available commands.")

    except ConnectionError as e:
        print(f"\n  ✗ Connection lost: {e}")
    finally:
        sock.close()
        print("  Connection closed. Goodbye!")


# ─── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_HOST
    port = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PORT
    run_client(host, port)
