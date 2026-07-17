#!/usr/bin/env python3
import argparse
import sys
import subprocess
import time
import re

def check_adb_connected():
    """Ensure at least one Android device is connected via ADB."""
    try:
        result = subprocess.run(["adb", "devices"], capture_output=True, text=True, check=True)
        lines = result.stdout.strip().split("\n")[1:]
        devices = [line for line in lines if line.strip() and "device" in line]
        if not devices:
            print("❌ Error: No Android device detected. Connect a phone with USB Debugging enabled.")
            sys.exit(1)
        return True
    except FileNotFoundError:
        print("❌ Error: ADB command line tool not found. Install Android Platform Tools or Homebrew adb.")
        sys.exit(1)

def interactive_coordinate_detection():
    """Automatically record, capture, and convert hex touch events to coordinates."""
    print("\n🔍 --- AUTOMATIC COORDINATE DETECTION ---")
    print("1. Please TAP ONCE on your phone screen exactly where you want to click.")
    print("2. The script will intercept the touch and automatically parse the location...")
    print("🤖 Listening to touch digitizer... Tap your screen now!")

    # Spin up getevent to read live interactions
    process = subprocess.Popen(
        ["adb", "shell", "getevent", "-l"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    hex_x, hex_y = None, None
    try:
        while True:
            line = process.stdout.readline()
            if not line:
                break
            
            # Watch for raw multi-touch coordinates
            if "ABS_MT_POSITION_X" in line:
                match = re.search(r"ABS_MT_POSITION_X\s+([0-9a-fA-F]+)", line)
                if match:
                    hex_x = match.group(1)
            elif "ABS_MT_POSITION_Y" in line:
                match = re.search(r"ABS_MT_POSITION_Y\s+([0-9a-fA-F]+)", line)
                if match:
                    hex_y = match.group(1)

            # Once we catch both coordinates from the single tap event sequence, terminate
            if hex_x and hex_y:
                process.terminate()
                break
    except KeyboardInterrupt:
        process.terminate()
        print("\nDetection canceled.")
        sys.exit(0)

    # Convert hexadecimal inputs to baseline raw integers
    raw_x = int(hex_x, 16)
    raw_y = int(hex_y, 16)

    # Get active Android Display Resolution (handles overrides and actual rendering size)
    active_res = subprocess.run(["adb", "shell", "dumpsys window displays"], capture_output=True, text=True)
    cur_match = re.search(r"cur=(\d+)x(\d+)", active_res.stdout)
    if cur_match:
        screen_w = int(cur_match.group(1))
        screen_h = int(cur_match.group(2))
    else:
        size_res = subprocess.run(["adb", "shell", "wm size"], capture_output=True, text=True)
        override_match = re.search(r"Override size:\s*(\d+)x(\d+)", size_res.stdout)
        if override_match:
            screen_w = int(override_match.group(1))
            screen_h = int(override_match.group(2))
        else:
            size_match = re.search(r"Physical size:\s*(\d+)x(\d+)", size_res.stdout)
            if not size_match:
                print("❌ Could not determine screen size. Defaulting to detected scale.")
                return raw_x, raw_y
            screen_w = int(size_match.group(1))
            screen_h = int(size_match.group(2))

    # Get hardware Digitizer Limits to safely map boundaries
    limits_res = subprocess.run(["adb", "shell", "getevent", "-p"], capture_output=True, text=True)
    # Target absolute position specs mapping for X (0035) and Y (0036)
    max_x_match = re.search(r"0035\s+:.*max\s+(\d+)", limits_res.stdout)
    max_y_match = re.search(r"0036\s+:.*max\s+(\d+)", limits_res.stdout)

    if max_x_match and max_y_match:
        digitizer_max_x = int(max_x_match.group(1))
        digitizer_max_y = int(max_y_match.group(1))
        
        # Align base physical display dimensions with the hardware digitizer orientation
        if digitizer_max_x > digitizer_max_y:
            base_w = max(screen_w, screen_h)
            base_h = min(screen_w, screen_h)
        else:
            base_w = min(screen_w, screen_h)
            base_h = max(screen_w, screen_h)

        base_x = (raw_x / digitizer_max_x) * base_w
        base_y = (raw_y / digitizer_max_y) * base_h

        # Detect active software rotation
        rotation_res = subprocess.run(["adb", "shell", "dumpsys window displays"], capture_output=True, text=True)
        rot_match = re.search(r"mCurrentRotation=(ROTATION_\d+)", rotation_res.stdout)
        rotation = rot_match.group(1) if rot_match else "ROTATION_0"

        # Map physical coordinates to active logical orientation for `input tap`
        if rotation == "ROTATION_90":
            scaled_x = base_y
            scaled_y = base_w - base_x
        elif rotation == "ROTATION_180":
            scaled_x = base_w - base_x
            scaled_y = base_h - base_y
        elif rotation == "ROTATION_270":
            scaled_x = base_h - base_y
            scaled_y = base_x
        else:
            scaled_x = base_x
            scaled_y = base_y

        scaled_x = int(scaled_x)
        scaled_y = int(scaled_y)
        
        print(f"✅ Success! Captured touch event:")
        print(f"   - Hex: ({hex_x}, {hex_y}) -> Raw Digitizer: ({raw_x}, {raw_y})")
        print(f"   - Active Rotation: {rotation} | Scaled Target: X={scaled_x}, Y={scaled_y}")
        return scaled_x, scaled_y
    else:
        # Fallback to direct calculation guess
        print(f"⚠️ Digitizer bounds hidden. Estimated raw tap at: X={raw_x}, Y={raw_y}")
        return raw_x, raw_y

def run_clicker(x, y, count, delay, double=False):
    """Executes clicking engine using optimized continuous subprocess streams."""
    action_name = "Double-Tap" if double else "Single-Tap"
    print(f"\n🚀 Starting engine: Targeting pixel ({x}, {y}) with {action_name}")
    print(f"⚙️ Queue Config: Actions={count if count > 0 else '∞ (Infinite)'} | Target Delay={delay}s")
    print("👉 Press [Control + C] on your Mac keyboard at any time to halt.")
    time.sleep(1.5)

    click_counter = 0
    start_time = time.time()

    def update_progress(clicks):
        elapsed_so_far = time.time() - start_time
        speed = clicks / elapsed_so_far if elapsed_so_far > 0 else 0
        sys.stdout.write(f"\r[+] Sent clicks: {clicks} | Speed: {speed:.2f} clicks/sec")
        sys.stdout.flush()

    cmd_action = f"input tap {x} {y}; input tap {x} {y}" if double else f"input tap {x} {y}"
    process = None
    try:
        # HIGH-SPEED EXECUTION CHANNEL
        # Instead of calling `adb shell` repeatedly, we run a native while loop straight
        # inside the Android operating system to optimize the frequency of touch instructions.
        if delay == 0 and count > 0:
            # Finite high-speed execution loop built completely inside Android shell bounds
            shell_cmd = f"i=0; while [ $i -lt {count} ]; do {cmd_action}; i=$((i+1)); echo $i; done"
            process = subprocess.Popen(["adb", "shell", shell_cmd], stdout=subprocess.PIPE, text=True)
            for line in process.stdout:
                if line.strip().isdigit():
                    click_counter = int(line.strip())
                    update_progress(click_counter)
            process.wait()

        elif delay == 0 and count == 0:
            # Infinite hyper-speed processing bypass
            shell_cmd = f"i=0; while true; do {cmd_action}; i=$((i+1)); echo $i; done"
            print("⚡ Running in MAX THROTTLE mode.")
            process = subprocess.Popen(["adb", "shell", shell_cmd], stdout=subprocess.PIPE, text=True)
            for line in process.stdout:
                if line.strip().isdigit():
                    click_counter = int(line.strip())
                    update_progress(click_counter)

        else:
            # CONTROLLED SPEED LOOP (Maintains strict tracking logs on your Mac)
            while count == 0 or click_counter < count:
                # Open process pipeline
                subprocess.run(["adb", "shell", cmd_action], capture_output=True)
                click_counter += 1
                
                update_progress(click_counter)
                
                if delay > 0:
                    time.sleep(delay)

    except KeyboardInterrupt:
        print("\n\n🛑 Script paused manually by user request.")
    finally:
        if process is not None:
            process.terminate()
    
    elapsed = time.time() - start_time
    print("\n\n📊 --- FINAL SESSION METRICS ---")
    if click_counter > 0:
        print(f"   - Completed Clicks: {click_counter}")
        print(f"   - Elapsed Time:     {elapsed:.2f} seconds")
        print(f"   - True Average Speed: {click_counter / elapsed:.2f} clicks/sec")
    print("---------------------------------\n")

def main():
    parser = argparse.ArgumentParser(
        description="🚀 High-Speed Android ADB Auto-Clicker Suite with Live Coordinate Scanner Engine.",
        epilog="Examples:\n"
               "  python3 autoclicker.py --detect --count 100 --delay 0\n"
               "  python3 autoclicker.py -x 815 -y 1780 --count 50 --delay 0.1",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument("-x", type=int, help="Target Screen coordinate X value (pixels).")
    parser.add_argument("-y", type=int, help="Target Screen coordinate Y value (pixels).")
    parser.add_argument("--detect", action="store_true", help="Launch interactive tool to find coordinates by touching your screen.")
    parser.add_argument("--count", type=int, default=10, help="Total execution clicks. Set to 0 to run indefinitely (default: 10).")
    parser.add_argument("--delay", type=float, default=0.0, help="Rest delay intervals in fractional seconds between taps (default: 0.0 for max speed).")
    parser.add_argument("--double", action="store_true", help="Perform a double-tap instead of a single tap for each execution cycle.")

    args = parser.parse_args()

    # Step 1: Enforce physical Android dependency connection checks
    check_adb_connected()

    # Step 2: Route coordinate parsing mechanisms
    target_x, target_y = args.x, args.y

    if target_x is None or target_y is None or args.detect:
        if not args.detect:
            print("⚠️ Coordinates not provided. Switching to interactive detection mode.")
        target_x, target_y = interactive_coordinate_detection()

    # Step 3: Launch click generator operations 
    run_clicker(target_x, target_y, args.count, args.delay, args.double)

if __name__ == "__main__":
    main()
