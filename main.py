import os
import time
import network
import ntptime
import ubinascii
import gc
import uasyncio as asyncio
from machine import Pin, SPI
import sdcard
from microdot import Microdot, Response, Request, send_file, urldecode

import secrets

EXPECTED_AUTH = ubinascii.b2a_base64(f"{AUTH_USER}:{AUTH_PASS}".encode()).decode().strip()

SD_MOUNT_PATH = "/sd"
LOG_FILE = "/sd/overwrite_log.txt"

# SPI0 Pins for SD Card
PIN_MISO = 16
PIN_CS   = 17
PIN_SCK  = 18
PIN_MOSI = 19

# Onboard LED on Pico W
led = Pin("LED", Pin.OUT)
led.off()

# Microdot Large File Streaming Configuration
Request.max_content_length = 2 * 1024 * 1024 * 1024  # Allow uploads up to 2 GB
Request.max_body_length = 16 * 1024                   # Force streaming for bodies > 16 KB
Response.default_content_type = 'text/html'
app = Microdot()

# ================= AUTHENTICATION MIDDLEWARE =================

@app.before_request
async def authenticate(request):
    """Require Basic HTTP Authentication for all browser and CLI requests."""
    auth = request.headers.get('Authorization')
    if not auth or not auth.startswith('Basic '):
        return Response(
            body="Authentication required.\n",
            status_code=401,
            headers={'WWW-Authenticate': 'Basic realm="Pico W File Server"'}
        )
    token = auth.split(' ', 1)[1].strip()
    if token != EXPECTED_AUTH:
        return Response(
            body="Invalid username or password.\n",
            status_code=401,
            headers={'WWW-Authenticate': 'Basic realm="Pico W File Server"'}
        )

# ================= HELPER FUNCTIONS =================

def init_sd_card():
    """Initialize SPI and mount the MicroSD card at high speed (12 MHz)."""
    try:
        # Start at 1 MHz for safe card initialization handshake
        spi = SPI(0, baudrate=1_000_000, polarity=0, phase=0,
                  sck=Pin(PIN_SCK), mosi=Pin(PIN_MOSI), miso=Pin(PIN_MISO))
        cs = Pin(PIN_CS, Pin.OUT)
        sd = sdcard.SDCard(spi, cs)
        
        # Boost SPI baudrate to 12 MHz for fast file transfers
        spi.init(baudrate=12_000_000, polarity=0, phase=0)
        
        vfs = os.VfsFat(sd)
        os.mount(vfs, SD_MOUNT_PATH)
        print(f"[OK] SD Card mounted at {SD_MOUNT_PATH} (12 MHz SPI)")
    except Exception as e:
        print(f"[ERROR] Failed to mount SD Card: {e}")

def connect_wifi():
    """Connect to Wi-Fi and sync RTC time via NTP."""
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    
    print("Connecting to Wi-Fi", end="")
    while not wlan.isconnected():
        led.toggle()
        time.sleep(0.3)
        print(".", end="")
    led.off()
    
    ip = wlan.ifconfig()[0]
    print(f"\n[OK] Connected! Pico W IP Address: http://{ip}")
    
    try:
        ntptime.settime()
        print("[OK] Time synced via NTP")
    except Exception as e:
        print(f"[WARN] NTP sync failed, using internal RTC: {e}")
    return ip

def get_timestamp():
    """Return formatted timestamp YYYY-MM-DD HH:MM:SS."""
    t = time.localtime()
    return f"{t[0]:04d}-{t[1]:02d}-{t[2]:02d} {t[3]:02d}:{t[4]:02d}:{t[5]:02d}"

def sanitize_path(rel_path):
    """Prevent directory traversal and decode percent-encoded URLs."""
    if not rel_path:
        return SD_MOUNT_PATH, ""
        
    # Decode percent-encoded characters (%20 -> space, etc.)
    decoded_path = urldecode(rel_path)
    
    clean_parts = [p for p in decoded_path.split("/") if p and p != ".."]
    clean_rel = "/".join(clean_parts)
    full_path = f"{SD_MOUNT_PATH}/{clean_rel}" if clean_rel else SD_MOUNT_PATH
    return full_path, clean_rel

def file_exists(path):
    """Check if a file or directory exists."""
    try:
        os.stat(path)
        return True
    except OSError:
        return False

def is_dir(path):
    """Check if path is a directory."""
    try:
        return (os.stat(path)[0] & 0x4000) != 0
    except OSError:
        return False

def ensure_dirs(full_dir_path):
    """Recursively create directories if they don't exist."""
    parts = [p for p in full_dir_path.split("/") if p]
    curr = ""
    for p in parts:
        curr += "/" + p
        if not file_exists(curr):
            os.mkdir(curr)

def rmtree(target_path):
    """Recursively delete a file or non-empty folder while toggling LED."""
    led.toggle()
    if is_dir(target_path):
        for entry in os.listdir(target_path):
            child_path = f"{target_path}/{entry}"
            rmtree(child_path)
        os.rmdir(target_path)
    else:
        os.remove(target_path)

def log_overwrite(rel_filepath):
    """Log file overwrite timestamps to /sd/overwrite_log.txt."""
    ts = get_timestamp()
    entry = f"[{ts}] OVERWRITTEN: /{rel_filepath}\n"
    try:
        with open(LOG_FILE, "a") as f:
            f.write(entry)
        print(entry.strip())
    except Exception as e:
        print(f"[ERROR] Logging failed: {e}")

async def blink_led_task(stop_event):
    """Background coroutine to blink onboard LED during file transfers."""
    try:
        while not stop_event.is_set():
            led.toggle()
            await asyncio.sleep_ms(80)
    finally:
        led.off()

@app.route('/check_log/<path:filepath>', methods=['GET'])
async def check_file_log(request, filepath):
    """Check overwrite history for a specific file without downloading the full log."""
    full_path, clean_rel = sanitize_path(filepath)
    
    if not file_exists(LOG_FILE):
        return "No overwrites recorded yet.\n", 200
    
    matches = []
    try:
        with open(LOG_FILE, "r") as f:
            for line in f:
                # Check if this log entry belongs to the requested file path
                if f"/{clean_rel}" in line:
                    matches.append(line.strip())
        
        if matches:
            return "\n".join(matches) + "\n", 200
        else:
            return f"File '/{clean_rel}' has never been overwritten.\n", 200
    except Exception as e:
        return f"Error reading log: {e}\n", 500

# ================= WEB UI GENERATOR =================

def render_directory_html(full_path, rel_path):
    """Generate HTML interface with conditional XHR progress bar for large files."""
    entries = []
    try:
        for name in os.listdir(full_path):
            item_full = f"{full_path}/{name}"
            item_rel = f"{rel_path}/{name}" if rel_path else name
            stat = os.stat(item_full)
            is_folder = (stat[0] & 0x4000) != 0
            size = stat[6]
            entries.append((name, item_rel, is_folder, size))
    except OSError as e:
        return f"<h1>Error reading directory: {e}</h1>"

    entries.sort(key=lambda x: (not x[2], x[0].lower()))
    parent_rel = "/".join(rel_path.split("/")[:-1]) if rel_path else None

    rows = ""
    if parent_rel is not None:
        parent_link = f"/?dir={parent_rel}" if parent_rel else "/"
        rows += f"""
        <tr>
            <td colspan="4"><a href="{parent_link}">📁 <strong>.. (Parent Directory)</strong></a></td>
        </tr>"""

    for name, item_rel, is_folder, size in entries:
        if is_folder:
            rows += f"""
            <tr>
                <td><a href="/?dir={item_rel}">📁 <strong>{name}/</strong></a></td>
                <td>Directory</td>
                <td>-</td>
                <td>
                    <button onclick="deleteItem('{item_rel}', true)" class="btn-del">Delete Folder</button>
                </td>
            </tr>"""
        else:
            size_str = f"{size / 1024:.1f} KB" if size >= 1024 else f"{size} B"
            rows += f"""
            <tr>
                <td>📄 {name}</td>
                <td>File</td>
                <td>{size_str}</td>
                <td>
                    <a href="/download/{item_rel}" class="btn-dl">Download</a>
                    <button onclick="deleteItem('{item_rel}', false)" class="btn-del">Delete</button>
                </td>
            </tr>"""

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Pico W SD File Server</title>
    <style>
        body {{ font-family: system-ui, sans-serif; max-width: 850px; margin: 20px auto; padding: 0 15px; background: #f4f6f8; color: #333; }}
        h2 {{ margin-bottom: 5px; }}
        .path-bar {{ background: #e2e8f0; padding: 10px; border-radius: 6px; margin-bottom: 15px; font-family: monospace; }}
        .card-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 20px; }}
        .card {{ background: #fff; padding: 15px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #eee; }}
        th {{ background: #2d3748; color: #fff; }}
        a {{ text-decoration: none; color: #2b6cb0; }}
        a:hover {{ text-decoration: underline; }}
        button, .btn-dl {{ padding: 5px 10px; border: none; border-radius: 4px; cursor: pointer; font-size: 13px; }}
        .btn-primary {{ background: #3182ce; color: white; padding: 8px 14px; }}
        .btn-primary:disabled {{ background: #a0aec0; cursor: not-allowed; }}
        .btn-dl {{ background: #38a169; color: white; display: inline-block; margin-right: 5px; }}
        .btn-del {{ background: #e53e3e; color: white; }}
        input[type="text"], input[type="file"] {{ width: 100%; margin-bottom: 10px; box-sizing: border-box; padding: 6px; }}
        
        #progressContainer {{ display: none; background: #fff; padding: 12px 15px; border-radius: 8px; margin-bottom: 15px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        .progress-track {{ width: 100%; height: 18px; background: #edf2f7; border-radius: 9px; overflow: hidden; margin-top: 6px; }}
        .progress-fill {{ height: 100%; width: 0%; background: linear-gradient(90deg, #3182ce, #38a169); transition: width 0.15s ease; }}
        .progress-stats {{ display: flex; justify-content: space-between; font-size: 13px; font-family: monospace; color: #2d3748; }}
    </style>
</head>
<body>
    <h2>🔒 Pico W MicroSD Server</h2>
    <div class="path-bar">
        Current Directory: <strong>/{rel_path}</strong>
        | <a href="/logs" style="float:right;">📋 View Overwrite Log</a>
    </div>

    <div id="progressContainer">
        <div class="progress-stats">
            <span id="progressText">Uploading... 0%</span>
            <span id="speedText">0.0 KB/s | ETA: --s</span>
        </div>
        <div class="progress-track">
            <div class="progress-fill" id="progressBar"></div>
        </div>
    </div>

    <div class="card-grid">
        <div class="card">
            <h4>Upload File</h4>
            <input type="file" id="fileInput">
            <button class="btn-primary" id="uploadBtn" onclick="uploadFile()">Upload to Current Folder</button>
        </div>
        <div class="card">
            <h4>Create New Folder</h4>
            <input type="text" id="folderInput" placeholder="Folder name...">
            <button class="btn-primary" onclick="createFolder()">Create Folder</button>
        </div>
    </div>

    <table>
        <thead>
            <tr><th>Name</th><th>Type</th><th>Size</th><th>Actions</th></tr>
        </thead>
        <tbody>
            {rows if rows else '<tr><td colspan="4">Directory is empty</td></tr>'}
        </tbody>
    </table>

    <script>
        const currentDir = "{rel_path}";

        function uploadFile() {{
            const fileInput = document.getElementById('fileInput');
            const uploadBtn = document.getElementById('uploadBtn');
            if (!fileInput.files.length) return alert("Select a file first!");

            const file = fileInput.files[0];
            const targetPath = currentDir ? currentDir + "/" + file.name : file.name;
            const encodedPath = targetPath.split('/').map(encodeURIComponent).join('/');

            // Only show progress bar for files larger than 256 KB
            const SHOW_BAR_THRESHOLD = 64 * 1024;
            const isLargeFile = file.size > SHOW_BAR_THRESHOLD;

            const progContainer = document.getElementById('progressContainer');
            const progBar = document.getElementById('progressBar');
            const progText = document.getElementById('progressText');
            const speedText = document.getElementById('speedText');

            uploadBtn.disabled = true;

            if (isLargeFile) {{
                progBar.style.width = '0%';
                progText.innerText = `Uploading ${{file.name}}... 0%`;
                speedText.innerText = `0.0 KB/s | ETA: --s`;
                progContainer.style.display = 'block';
            }} else {{
                progContainer.style.display = 'none';
                uploadBtn.innerText = "Uploading...";
            }}

            const xhr = new XMLHttpRequest();
            const startTime = Date.now();

            if (isLargeFile) {{
                xhr.upload.onprogress = function(e) {{
                    if (e.lengthComputable) {{
                        const percent = ((e.loaded / e.total) * 100).toFixed(1);
                        const elapsedSec = (Date.now() - startTime) / 1000;
                        const loadedKB = e.loaded / 1024;
                        const totalKB = e.total / 1024;
                        const speedKBs = elapsedSec > 0 ? (loadedKB / elapsedSec) : 0;
                        const remainingKB = totalKB - loadedKB;
                        const etaSec = speedKBs > 0 ? Math.ceil(remainingKB / speedKBs) : 0;

                        progBar.style.width = percent + '%';
                        progText.innerText = `Uploading ${{file.name}}: ${{percent}}% (${{loadedKB.toFixed(0)}} / ${{totalKB.toFixed(0)}} KB)`;
                        speedText.innerText = `${{speedKBs.toFixed(1)}} KB/s | ETA: ${{etaSec}}s`;
                    }}
                }};
            }}

            xhr.onload = function() {{
                uploadBtn.disabled = false;
                uploadBtn.innerText = "Upload to Current Folder";
                if (xhr.status >= 200 && xhr.status < 300) {{
                    if (isLargeFile) {{
                        progBar.style.width = '100%';
                        progText.innerText = 'Upload Complete! Refreshing...';
                    }}
                    setTimeout(() => {{
                        alert(xhr.responseText);
                        location.reload();
                    }}, 100);
                }} else {{
                    alert("Upload error (" + xhr.status + "): " + xhr.responseText);
                }}
            }};

            xhr.onerror = function() {{
                uploadBtn.disabled = false;
                uploadBtn.innerText = "Upload to Current Folder";
                alert("Network error occurred during upload.");
            }};

            xhr.open('PUT', '/upload/' + encodedPath, true);
            xhr.send(file);
        }}

        async function createFolder() {{
            const folderName = document.getElementById('folderInput').value.trim();
            if (!folderName) return alert("Enter a folder name!");
            const targetPath = currentDir ? currentDir + "/" + folderName : folderName;
            const encodedPath = targetPath.split('/').map(encodeURIComponent).join('/');
            
            const res = await fetch('/mkdir/' + encodedPath, {{ method: 'POST' }});
            const text = await res.text();
            alert(text);
            location.reload();
        }}

        async function deleteItem(path, isFolder) {{
            const msg = isFolder 
                ? "WARNING: Recursively delete folder '/" + path + "' and ALL its contents?" 
                : "Delete file '/" + path + "'?";
            if (!confirm(msg)) return;
            const encodedPath = path.split('/').map(encodeURIComponent).join('/');
            const res = await fetch('/delete/' + encodedPath, {{ method: 'DELETE' }});
            const text = await res.text();
            alert(text);
            location.reload();
        }}
    </script>
</body>
</html>"""
    return html

# ================= HTTP ROUTES =================

@app.route('/', methods=['GET'])
async def index(request):
    rel_dir = request.args.get('dir', '')
    full_path, clean_rel = sanitize_path(rel_dir)

    if not file_exists(full_path) or not is_dir(full_path):
        return "Directory not found\n", 404

    accept = request.headers.get('Accept', '')
    user_agent = request.headers.get('User-Agent', '')
    if 'application/json' in accept or 'curl' in user_agent.lower():
        items = []
        for name in os.listdir(full_path):
            st = os.stat(f"{full_path}/{name}")
            items.append({
                "name": name,
                "type": "dir" if (st[0] & 0x4000) else "file",
                "size": st[6],
                "mtime": st[8]
            })
        return {"directory": f"/{clean_rel}", "contents": items}

    return render_directory_html(full_path, clean_rel)

# --- OPTIMIZATION: Increased Upload Buffer Size ---
@app.route('/upload/<path:filepath>', methods=['PUT', 'POST'])
async def upload_file(request, filepath):
    full_path, clean_rel = sanitize_path(filepath)
    if not clean_rel:
        return "Invalid file path\n", 400

    parent_dir = "/".join(full_path.split("/")[:-1])
    ensure_dirs(parent_dir)
    overwritten = file_exists(full_path) and not is_dir(full_path)

    content_length = request.headers.get('Content-Length')
    total_bytes_expected = int(content_length) if content_length else -1

    gc.collect()
    bytes_written = 0
    # Increased chunk size from 2048 to 8192 (8 KB)
    chunk_size = 8192  

    try:
        with open(full_path, "wb") as f:
            while True:
                to_read = chunk_size
                if total_bytes_expected > 0:
                    remaining = total_bytes_expected - bytes_written
                    if remaining <= 0:
                        break
                    if to_read > remaining:
                        to_read = remaining

                # Async read naturally yields control
                chunk = await request.stream.read(to_read)
                if not chunk:
                    break
                
                f.write(chunk)
                bytes_written += len(chunk)
                
                # Moderate flush/gc to balance memory safety and speed
                if bytes_written % (256 * 1024) < chunk_size:
                    f.flush()
                    gc.collect()

        if overwritten:
            log_overwrite(clean_rel)
        msg = f"File '/{clean_rel}' ({bytes_written} bytes) {'overwritten' if overwritten else 'uploaded'}.\n"
        return msg, 200
        
    except Exception as e:
        return f"Upload failed mid-transfer: {e}\n", 500
    finally:
        gc.collect()

# --- OPTIMIZATION: Increased Download Buffer Size ---
@app.route('/download/<path:filepath>', methods=['GET'])
async def download_file(request, filepath):
    full_path, clean_rel = sanitize_path(filepath)

    if not file_exists(full_path) or is_dir(full_path):
        return "File not found\n", 404

    filename = clean_rel.split("/")[-1]
    
    try:
        file_size = os.stat(full_path)[6]
    except OSError:
        file_size = 0

    def file_stream():
        # Increased buffer from 4096 to 8192 (8 KB)
        buf = bytearray(8192)  
        try:
            with open(full_path, "rb") as f:
                while True:
                    size = f.readinto(buf)
                    if size == 0:
                        break
                    yield memoryview(buf)[:size]
        finally:
            gc.collect()

    return Response(
        body=file_stream(),
        headers={
            'Content-Type': 'application/octet-stream',
            'Content-Length': str(file_size),
            'Content-Disposition': f'attachment; filename="{filename}"'
        }
    )

@app.route('/mkdir/<path:dirpath>', methods=['POST', 'PUT'])
async def make_directory(request, dirpath):
    full_path, clean_rel = sanitize_path(dirpath)
    if not clean_rel:
        return "Invalid folder name\n", 400
    if file_exists(full_path):
        return f"Path '/{clean_rel}' already exists.\n", 400
    try:
        ensure_dirs(full_path)
        return f"Folder '/{clean_rel}' created successfully.\n", 201
    except Exception as e:
        return f"Error creating folder: {e}\n", 500

@app.route('/delete/<path:filepath>', methods=['DELETE', 'POST'])
async def delete_item(request, filepath):
    full_path, clean_rel = sanitize_path(filepath)
    
    if not clean_rel or full_path == SD_MOUNT_PATH:
        return "Cannot delete root SD directory.\n", 400

    if not file_exists(full_path):
        return "File or folder not found.\n", 404

    try:
        folder_flag = is_dir(full_path)
        rmtree(full_path)
        led.off()
        if folder_flag:
            return f"Folder '/{clean_rel}' and all its contents deleted.\n", 200
        return f"File '/{clean_rel}' deleted.\n", 200
    except OSError as e:
        led.off()
        return f"Delete failed: {e}\n", 400

@app.route('/logs', methods=['GET'])
async def view_logs(request):
    if not file_exists(LOG_FILE):
        return Response("No files have been overwritten yet.\n", headers={'Content-Type': 'text/plain'})
    return send_file(LOG_FILE, content_type='text/plain')

# ================= MAIN ENTRY POINT =================

async def main():
    init_sd_card()
    ip = connect_wifi()
    print(f"Starting server on http://{ip}:80 ...")
    await app.start_server(host='0.0.0.0', port=80, debug=True)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        led.off()
        print("Server stopped.")
