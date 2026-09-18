import socket
import threading
import os
import logging
from datetime import datetime

# ─── Configuration ────────────────────────────────────────────────────────────
HOST = "0.0.0.0"
PORT = 9000
BUFFER_SIZE = 4096
SERVER_DIR = "server_files"
LOG_FILE = "server.log"

# ─── Logging Setup ────────────────────────────────────────────────────────────
os.makedirs(SERVER_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ─── Helper: Send a text line ──────────────────────────────────────────────────
def send_line(conn, message):
    conn.sendall((message + "\n").encode())


# ─── Command Handlers ──────────────────────────────────────────────────────────

def handle_list(conn, addr):
    """Send a list of all files in the server directory."""
    try:
        files = os.listdir(SERVER_DIR)
        files = [f for f in files if os.path.isfile(os.path.join(SERVER_DIR, f))]
        if not files:
            send_line(conn, "200 OK 0")
            logger.info(f"[{addr}] LIST → 200 OK (0 files)")
        else:
            send_line(conn, f"200 OK {len(files)}")
            for f in files:
                size = os.path.getsize(os.path.join(SERVER_DIR, f))
                send_line(conn, f"{f} {size}")
            logger.info(f"[{addr}] LIST → 200 OK ({len(files)} files)")
    except Exception as e:
        send_line(conn, "500 SERVER_ERROR")
        logger.error(f"[{addr}] LIST → 500 SERVER_ERROR: {e}")


def handle_get(conn, addr, filename):
    """Send a file to the client in chunks."""
    safe_name = os.path.basename(filename)
    filepath = os.path.join(SERVER_DIR, safe_name)

    if not os.path.exists(filepath):
        send_line(conn, "404 FILE_NOT_FOUND")
        logger.warning(f"[{addr}] GET {filename} → 404 FILE_NOT_FOUND")
        return

    if not os.access(filepath, os.R_OK):
        send_line(conn, "403 PERMISSION_DENIED")
        logger.warning(f"[{addr}] GET {filename} → 403 PERMISSION_DENIED")
        return

    try:
        file_size = os.path.getsize(filepath)
        send_line(conn, f"200 OK {file_size}")

        bytes_sent = 0
        with open(filepath, "rb") as f:
            while chunk := f.read(BUFFER_SIZE):
                conn.sendall(chunk)
                bytes_sent += len(chunk)

        logger.info(f"[{addr}] GET {filename} → 200 OK ({bytes_sent} bytes sent)")
    except (BrokenPipeError, ConnectionResetError):
        logger.error(f"[{addr}] GET {filename} → CONNECTION_LOST during transfer")
    except Exception as e:
        logger.error(f"[{addr}] GET {filename} → 500 SERVER_ERROR: {e}")


def handle_put(conn, addr, parts):
    """Receive a file from the client in chunks."""
    if len(parts) < 3:
        send_line(conn, "400 BAD_REQUEST missing filename or size")
        return

    filename = os.path.basename(parts[1])
    try:
        file_size = int(parts[2])
    except ValueError:
        send_line(conn, "400 BAD_REQUEST invalid file size")
        return

    filepath = os.path.join(SERVER_DIR, filename)

    try:
        send_line(conn, "200 READY")

        bytes_received = 0
        with open(filepath, "wb") as f:
            while bytes_received < file_size:
                remaining = file_size - bytes_received
                chunk = conn.recv(min(BUFFER_SIZE, remaining))
                if not chunk:
                    break
                f.write(chunk)
                bytes_received += len(chunk)

        if bytes_received == file_size:
            send_line(conn, f"200 OK {bytes_received}")
            logger.info(f"[{addr}] PUT {filename} → 200 OK ({bytes_received} bytes received)")
        else:
            send_line(conn, "500 INCOMPLETE_TRANSFER")
            logger.error(f"[{addr}] PUT {filename} → INCOMPLETE ({bytes_received}/{file_size} bytes)")
    except (BrokenPipeError, ConnectionResetError):
        logger.error(f"[{addr}] PUT {filename} → CONNECTION_LOST during transfer")
    except OSError as e:
        send_line(conn, "500 DISK_ERROR")
        logger.error(f"[{addr}] PUT {filename} → DISK_ERROR: {e}")


def handle_delete(conn, addr, filename):
    """Delete a file from the server directory."""
    safe_name = os.path.basename(filename)
    filepath = os.path.join(SERVER_DIR, safe_name)

    if not os.path.exists(filepath):
        send_line(conn, "404 FILE_NOT_FOUND")
        logger.warning(f"[{addr}] DELETE {filename} → 404 FILE_NOT_FOUND")
        return

    try:
        os.remove(filepath)
        send_line(conn, "200 OK DELETED")
        logger.info(f"[{addr}] DELETE {filename} → 200 OK")
    except PermissionError:
        send_line(conn, "403 PERMISSION_DENIED")
        logger.warning(f"[{addr}] DELETE {filename} → 403 PERMISSION_DENIED")
    except Exception as e:
        send_line(conn, "500 SERVER_ERROR")
        logger.error(f"[{addr}] DELETE {filename} → 500 SERVER_ERROR: {e}")


# ─── Per-Client Handler ────────────────────────────────────────────────────────

def handle_client(conn, addr):
    addr_str = f"{addr[0]}:{addr[1]}"
    logger.info(f"[{addr_str}] CONNECTED")
    send_line(conn, "220 FTP-Like Server Ready")

    try:
        buffer = ""
        while True:
            data = conn.recv(BUFFER_SIZE)
            if not data:
                break

            buffer += data.decode(errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                command = line.strip()
                if not command:
                    continue

                parts = command.split()
                cmd = parts[0].upper()

                if cmd == "LIST":
                    handle_list(conn, addr_str)
                elif cmd == "GET" and len(parts) >= 2:
                    handle_get(conn, addr_str, parts[1])
                elif cmd == "PUT" and len(parts) >= 3:
                    handle_put(conn, addr_str, parts)
                elif cmd == "DELETE" and len(parts) >= 2:
                    handle_delete(conn, addr_str, parts[1])
                elif cmd == "QUIT":
                    send_line(conn, "221 GOODBYE")
                    logger.info(f"[{addr_str}] QUIT")
                    return
                else:
                    send_line(conn, "400 UNKNOWN_COMMAND")
                    logger.warning(f"[{addr_str}] UNKNOWN COMMAND: {command}")

    except (ConnectionResetError, BrokenPipeError):
        logger.warning(f"[{addr_str}] CONNECTION_LOST")
    except Exception as e:
        logger.error(f"[{addr_str}] ERROR: {e}")
    finally:
        conn.close()
        logger.info(f"[{addr_str}] DISCONNECTED")


# ─── Main Server Loop ──────────────────────────────────────────────────────────

def start_server():
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST, PORT))
    server_sock.listen(10)

    logger.info(f"Server listening on {HOST}:{PORT}")
    logger.info(f"Serving files from: {os.path.abspath(SERVER_DIR)}")
    logger.info("Waiting for connections... (Ctrl+C to stop)\n")

    try:
        while True:
            conn, addr = server_sock.accept()
            thread = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            thread.start()
    except KeyboardInterrupt:
        logger.info("Server shutting down.")
    finally:
        server_sock.close()


if __name__ == "__main__":
    start_server()
