import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
import time 
rclpy.init()
node = Node('move_agv')
pub = node.create_publisher(Twist,'/cmd_vel',10)
x = 0
y = 0
def callback(msg):
    global x,y
    x = msg.pose.pose.position.x
    y = msg.pose.pose.position.y
msg = Twist()

node = Node('odom_reader')
node.create_subscription(Odometry,'/odom',callback,10)


rclpy.spin_once(node)
print(x,y)
msg.linear.x=0.1
pub.publish(msg)

time.sleep(2)
msg.linear.x=0.0
pub.publish(msg)

rclpy.spin_once(node)
print(x,y)
