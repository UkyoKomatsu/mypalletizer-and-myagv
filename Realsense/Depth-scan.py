import pyrealsense2 as rs
import numpy as np
import cv2

pipeline = rs.pipeline()
config = rs.config()
config.enable_stream(rs.stream.depth, 848, 480, rs.format.z16, 30)

colorizer = rs.colorizer()
colorizer.set_option(rs.option.color_scheme, 0)  # Jet
colorizer.set_option(rs.option.min_distance, 0.0)
colorizer.set_option(rs.option.max_distance, 4.0)
colorizer.set_option(rs.option.histogram_equalization_enabled, 0)

depth = None
mouse_x, mouse_y = 0, 0

def mouse_callback(event, x, y, flags, param):
    global mouse_x, mouse_y
    mouse_x, mouse_y = x, y

cv2.namedWindow("Depth")
cv2.setMouseCallback("Depth", mouse_callback)

pipeline.start(config)

try:
    while True:
        frames = pipeline.wait_for_frames()
        depth = frames.get_depth_frame()

        if not depth:
            continue

        # Depthをカラー画像に変換
        color_frame = colorizer.colorize(depth)
        image = np.asanyarray(color_frame.get_data())
        image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

        # マウス位置の距離を取得
        if 0 <= mouse_x < 848 and 0 <= mouse_y < 480:
            distance = depth.get_distance(mouse_x, mouse_y)

            cv2.putText(
                image,
                f"({mouse_x}, {mouse_y}) : {distance:.3f} m",
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 255),
                2
            )

        cv2.imshow("Depth", image)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

finally:
    pipeline.stop()
    cv2.destroyAllWindows()