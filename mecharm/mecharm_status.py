"""Read-only mechArm socket diagnostics. No movement or servo release."""

from importlib.metadata import version
import socket
import time

from pymycobot import MechArmSocket


class ReliableMechArmSocket(MechArmSocket):
    """Binary robot commands with a complete, matching reply frame."""

    reply_timeout = 1.0

    def connect_socket(self):
        self._reply_buffer = bytearray()
        return socket.create_connection((self.SERVER_IP, self.SERVER_PORT), timeout=5)

    def _read(self, genre, method=None, **kwargs):
        # The server can wait 0.5s; the SDK's default socket wait is only 0.3s.
        deadline = time.monotonic() + self.reply_timeout
        while True:
            # TCP may split one frame or combine several frames in one recv().
            while len(self._reply_buffer) >= 2:
                start = self._reply_buffer.find(b"\xfe\xfe")
                if start < 0:
                    self._reply_buffer[:] = (
                        b"\xfe" if self._reply_buffer[-1:] == b"\xfe" else b""
                    )
                    break
                del self._reply_buffer[:start]
                if len(self._reply_buffer) < 3:
                    break
                length = self._reply_buffer[2]
                if length < 2:
                    del self._reply_buffer[0]
                    continue
                frame_size = length + 3
                if len(self._reply_buffer) < frame_size:
                    break
                if self._reply_buffer[frame_size - 1] != 0xFA:
                    del self._reply_buffer[0]
                    continue
                frame = bytes(self._reply_buffer[:frame_size])
                del self._reply_buffer[:frame_size]
                if frame[3] != genre:
                    self.log.debug("Skipped reply 0x%02x; waiting for 0x%02x",
                                   frame[3], genre)
                    continue
                self.log.debug("_read frame: %s", frame.hex(" "))
                return frame

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.log.debug("Reply timeout for 0x%02x", genre)
                return b""
            self.sock.settimeout(remaining)
            try:
                chunk = self.sock.recv(1024)
            except socket.timeout:
                self.log.debug("Reply timeout for 0x%02x", genre)
                return b""
            if not chunk:
                raise ConnectionError("Robot server closed the connection")
            self._reply_buffer.extend(chunk)


def main():
    print("=== STATE CHECK ONLY: no movement, no servo enable ===", flush=True)
    mc = ReliableMechArmSocket("192.168.128.150", 9000, debug=True)
    try:
        for name in ("is_power_on", "is_all_servo_enable", "is_paused",
                     "is_moving", "get_error_information", "get_angles",
                     "get_coords", "get_servo_status"):
            print(f"{name}: {getattr(mc, name)()!r}", flush=True)
            time.sleep(0.2)
        print("JOINT 3 LIMITS:", mc.get_joint_min_angle(3),
              mc.get_joint_max_angle(3), flush=True)
    finally:
        mc.sock.close()
        print("=== State check complete ===", flush=True)

if __name__ == "__main__":
    main()
