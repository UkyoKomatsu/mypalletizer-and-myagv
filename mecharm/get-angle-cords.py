from pymycobot import MechArmSocket
import time
# Port 9000 is used by default
mc = MechArmSocket("192.168.128.150",9000)
print(mc.get_angles())
print(mc.get_coords())
