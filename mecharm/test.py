from pymycobot import MechArmSocket
import time
# Port 9000 is used by default
mc = MechArmSocket("192.168.128.150",9000)
mc.clear_error_information()
error = mc.get_error_information()
print("robot error:", error)
# if error != 0:
#     mc.sock.close()
#     raise RuntimeError(f"Robot error {error}: resolve the robot state before movement")

res = mc.get_angles()
# mc.send_angles([0,0,0,0,0,0],50)
time.sleep(2)
print(res)
mc.set_gripper_state(0,50,1)
mc.send_coords([75.1, 0, 154.0, -179, 30, -179],50,0,5)
time.sleep(6)
error = mc.get_error_information()
print(f"エラー：{error}")
res = mc.get_coords()
print(res)

# time.sleep(4)
# mc.release_all_servos()