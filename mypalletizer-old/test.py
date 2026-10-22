from pymycobot import MyPalletizerSocket
import time
mc = MyPalletizerSocket("10.42.10.101",9000)

print(mc.get_angles())
mc.sync_send_angles([0,0,0,0],40,15)
time.sleep(3)
mc.release_all_servos()
