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

# 2. フィルタの初期化
spatial = rs.spatial_filter()
temporal = rs.temporal_filter()
hole_filling = rs.hole_filling_filter()

depth = None
mouse_x, mouse_y = 0, 0

profile = pipeline.start(config)
device = profile.get_device()
depth_sensor = device.first_depth_sensor()
advanced_mode = rs.rs400_advanced_mode(device)

if not advanced_mode.is_enabled():
    advanced_mode.toggle_advanced_mode(True)

# --- 近距離用のパラメータ修正設定 ---
if depth_sensor.supports(rs.option.emitter_enabled):
    depth_sensor.set_option(rs.option.emitter_enabled, 1)  # 1:ON, 0:OFF

# 15cmだと200は強すぎて白飛びするため、レーザーパワーを大幅に下げます
if depth_sensor.supports(rs.option.laser_power):
    depth_sensor.set_option(rs.option.laser_power, 200)  

# 【修正】Advanced Mode を介して Disparity Shift を 120 に設定
depth_table = advanced_mode.get_depth_table()
depth_table.disparityShift = 300
advanced_mode.set_depth_table(depth_table)


def mouse_callback(event, x, y, flags, param):
    global mouse_x, mouse_y
    mouse_x, mouse_y = x, y

cv2.namedWindow("Depth")
cv2.setMouseCallback("Depth", mouse_callback)


try:
    while True:
        # 【追加】カメラから最新のフレームデータを取得する処理
        frames = pipeline.wait_for_frames()
        raw_depth_frame = frames.get_depth_frame()

        if not raw_depth_frame:
            continue

        # フィルタを順番に適用する
        filtered_frame = spatial.process(raw_depth_frame)
        filtered_frame = temporal.process(filtered_frame)
        filtered_frame = hole_filling.process(filtered_frame)

        # 【修正】フィルタ適用後のフレームを明示的に Depth Frame 型にキャストする
        filtered_depth_frame = filtered_frame.as_depth_frame()

        # フィルタ適用後の綺麗なDepthフレームをカラー画像に変換
        color_frame = colorizer.colorize(filtered_depth_frame)
        image = np.asanyarray(color_frame.get_data())
        image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

        # マウス位置の距離を取得
        if 0 <= mouse_x < 848 and 0 <= mouse_y < 480:
            # 【修正】キャスト後のオブジェクトから get_distance を呼び出す
            distance = filtered_depth_frame.get_distance(mouse_x, mouse_y)

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
