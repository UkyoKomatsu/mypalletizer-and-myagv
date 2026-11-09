# D435 研究用データ収集プログラム

ライブ表示を見ながら、任意のタイミングで「保存」を押すと、表示していたフレームの画像・点群・設定を一組で保存します。通常の表示中は、撮影データをディスクに書き込みません。Windows向けの起動ファイルを同梱し、PythonプログラムはLinuxでも利用できる構成です。

## 最初の起動（Windows）

1. ZIPを展開します。日本語のフォルダ名でも利用できます。
2. Python 3.11を用意し、`setup_windows.bat`を実行します。必要なライブラリを、このフォルダの`.venv`へインストールします。初回はインターネット接続が必要です。
3. RealSense Viewerなど、カメラを使っている別のソフトを終了し、D435をUSB 3接続します。
4. `launch_windows.bat`を実行し、「D435に接続」を押します。
5. 映像が出たら、保存先・試料名・メモを設定します。
6. 任意のタイミングで「保存」を押します。Ctrl+Sでも同じ操作になります。

通常の保存枚数は1です。点群が必要な瞬間に1回押すと、表示中のフレームを保存します。保存完了までボタンを無効化し、二重保存を防ぎます。設定変更直後は20フレームを捨て、新しい条件の映像が出てから保存を受け付けます。移行フレームを捨てる処理であり、画像のノイズフィルタではありません。

ソースから手動で起動する場合：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

カメラなしで保存操作を試す場合：

```powershell
.\.venv\Scripts\python.exe app.py --demo
```

デモのデータは合成画像です。画面・試料名・保存先・メタデータで実測データと区別しています。デモだけなら`requirements-demo.txt`で起動でき、RealSense SDKは不要です。LinuxではディストリビューションのTkinter（例：python3-tk）も必要です。

## 画面と保存ボタン

- ライブ表示：Depth、RGB、左IR、右IR。
- Depthの表示範囲：0.10～0.60m。0は黒。色の範囲外でも保存データは切り捨てません。
- 保存：ボタンを押したときに表示していたフレームを最初の1枚として保存。
- 保存枚数：1～60。2枚以上なら、その後のフレームも指定枚数だけ保存。
- ROI：Depthをドラッグして「参照面」と「障害物下」を指定。ROIは集計範囲で、保存される画像や点群は全画面です。
- 点群表示：「保存した点群を見る」から、その撮影の`frame_0000/arrays.npz`などを選択。ドラッグで回転、ホイールで拡大。表示だけ最大4万点に間引き、保存データは変更しません。
- 撮影条件：プロジェクタON/OFF、レーザー出力、自動／手動露光、ゲイン、Disparity Shift、Depth Units。

点群表示はXYZ形状の確認用です。RGBによる彩色やRGBとDepthの画素対応の正しさを検証する機能ではありません。

## 約20cmでの開始設定

初期値はDepth・左右IRが640×480・30FPS、RGBが640×480・30FPS、レーザー出力60mW、自動露光ON、Disparity Shift=0、Depth Units=100µmです。60mWは調整の開始値であり、実機で最適化した値ではありません。

D435の20cm前後は、解像度・設定・物体形状によって近距離の制約が効く範囲です。公式資料は、近距離ではDisparity Shiftなどの調整が使える一方、焦点や左右画像の見え方にも制約があると説明しています。このプログラムも、20cmで全対象のDepthが得られることは保証しません。[RealSense公式：Tuning depth cameras](https://dev.realsenseai.com/docs/tuning-depth-cameras-for-best-performance/)

Depth Units=100µmは、近距離の変動が1mm単位への量子化で隠れにくいようにした開始設定です。物理的な精度を0.1mmにする機能ではありません。1000µmなどにも変更でき、実際の換算係数を毎フレームのメタデータに保存します。距離[m]は、Z16保存値×`depth_scale_m`で計算します。

## ノイズと、残したい欠損の扱い

保存するDepthに、Hole Filling・空間平滑化・時間平均・間引き・距離閾値・点群の外れ値除去を適用しません。時間変動の統計は別ファイルに保存し、元の画素は変更しません。ここでいう生データは「SDKが返すZ16・IR・RGB」で、ASIC内部処理より前のセンサ電気信号ではありません。

レーザーのスペックルは左右の見え方を変え、Depth誤差の原因になり得ます。しかし、黒い欠損の全てがレーザー由来とは限らず、出力を下げれば必ず改善するとも限りません。投光を弱めることで模様不足になり、逆に欠損が増える場合もあります。ソフトウェアだけでレーザー由来の欠損を完全に取り除き、障害物下の欠損だけを必ず残すことはできません。[公式：Projectors for D400 Series Depth Cameras](https://dev.realsenseai.com/docs/projectors/)

このため、撮影条件を変えて原因を調べられるようにしています。次の手順を推奨します。

1. カメラ・対象・照明を固定し、参照面ROIと障害物下ROIを指定します。
2. 映像で露光を調整し、自動露光をOFFにして露光・ゲインを固定します。
3. プロジェクタOFF、ONで30mW・60mW・90mWなどを比較します。カメラが受け付ける範囲・刻みで適用され、実際の値は保存されます。
4. 各条件で、保存枚数を30などにしてボタンを押します。試料名に条件を書きます。
5. 参照面の孤立した欠損、IRの飽和、距離の時間変動がどう変わるか調べます。同時に障害物下ROIの欠損の広がり・出現頻度も比較します。

「孤立0画素」は、上下左右斜めの8近傍すべてが有効な、単独の欠損を数えたものです。レーザー由来という判定ではなく、画素を削除・補間する処理でもありません。IRの黒に近い割合・飽和に近い割合も記述的な指標です。

## 距離によるDisparity Shiftの切替

距離だけから最適なShiftを計算する方式は採っていません。対象・解像度・背景で確認した値を、距離の範囲に登録する方式です。

1. 例えば20cm付近で、Shiftを手動変更して「設定を適用」を押し、対象と背景のDepthを確認します。
2. 実際に使う距離範囲（例：18～22cm）を入力します。
3. 「現在の適用済みShiftを登録」を押します。入力欄で未適用の値ではなく、表示フレームに使われた値が登録されます。
4. 別の距離範囲でも同じ操作をします。異なる範囲の境界は重ねないでください。
5. 「対象距離」を変更し、「対象距離の登録Shiftを適用」を押すと、その距離の設定へ切り替わります。

登録は`distance_profiles.json`です。同じカメラのシリアル番号・Depth解像度・FPSの組み合わせにだけ適用します。未登録・範囲重複の場合は適用しません。登録は利用者が選んだ設定という意味で、自動的な精度保証ではありません。デモ用の登録は実カメラには適用されません。

**対象距離は利用者の入力値です。ライブ映像から距離を推定して全自動追従する機能ではありません。** 欠損や範囲外の誤距離でShiftを自動的に動かし続けることを避け、まず再現可能な条件でデータを集める構成です。

Shiftを増やすと近距離側の探索範囲を変えられますが、遠方の測距範囲は狭くなります。欠損・誤対応が変化するので、研究ではShiftを交絡要因として記録し、同じ条件で比較してください。[公式サポート：近距離とDisparity Shift](https://support.realsenseai.com/hc/en-us/community/posts/49790696762259-Focus-Calibration-for-closeish-range-D435)

## 保存されるファイル

保存ボタンを押すごとに、日時・条件名・識別子から成る新しいフォルダを作ります。既存撮影への上書きはしません。

```text
captures/
  日時_条件名_識別子/
    capture.json
    checksums.sha256
    frame_0000/
      depth_z16.raw
      depth_z16.png
      ir_left.png
      ir_right.png
      rgb_unaligned.png
      invalid_mask.png
      depth_preview.png
      arrays.npz
      points.ply
      metadata.json
    frame_0001/ ...                指定枚数が2以上の場合
    temporal_statistics.npz       指定枚数が2以上の場合
    invalid_frequency.png         指定枚数が2以上の場合
```

- `depth_z16.raw`：リトルエンディアンuint16、行優先。サイズはメタデータの解像度×2バイト。
- `depth_z16.png`：16bitの距離保存値。カラー画像ではありません。
- `invalid_mask.png`：255が欠損、0が有効。
- `depth_preview.png`：表示用の色付き画像。距離の解析にはZ16を使います。
- `arrays.npz`：`depth_z16`、`xyz_m`、`valid_mask`、左右IR、未整列RGB。`xyz_m`はH×W×3の形で、元の画素に対応します。欠損位置はNaNです。
- `points.ply`：有効点だけのXYZ点群、バイナリPLY。単位m。色は付けていません。
- 座標系：Depthの光学座標。+Xは右、+Yは下、+Zは前。床基準・ロボット基準への変換は別途必要です。
- `metadata.json`：カメラ・ファームウェア、Depth単位、各ストリームの内部パラメータと歪み係数、Depthから各カメラへの外部パラメータ、フレーム番号・時刻・時刻ドメイン、フレームメタデータ、センサ設定、Advanced Mode全設定、ROI指標。
- `sensor_options`の露光・ゲインは設定適用時の読取値です。自動露光中の実フレームの値は、各フレームの`frames.*.metadata`を確認してください。
- `temporal_statistics.npz`：画素ごとの有効サンプル数、平均距離m、標本標準偏差m、欠損出現頻度。平均は有効値のみ、標準偏差は2個以上の有効値がある場所のみ。それ以外はNaN。
- RGBは未整列です。Depth・左IRとの同一画素位置への対応付けにはキャリブレーションを使います。各画像は同じSDKフレームセット由来ですが、各ストリームの時刻も別々に記録し、同時露光と決めつけません。

複数枚保存は、取得・保存を順に行います。ディスク書き込みの遅延を含むため、30FPSの連続全フレームを保証する録画ではありません。実際の間隔は保存されたフレーム時刻から判断してください。保存中の中断・障害では`.partial`フォルダと`INCOMPLETE.json`を残し、完了撮影と区別します。

## データ分析を始める

撮影フォルダ群を集計し、新しい出力フォルダにCSVを書き出します。

```powershell
.\.venv\Scripts\python.exe analyze.py captures --out analysis_01
```

`analysis_01/analysis_summary.csv`に、撮影条件・フレーム時刻・ROI別の欠損率・孤立0画素数・距離中央値・IR飽和率・時間標準偏差などが出ます。CSVがすでにある場合は上書きせず停止します。デモは`captures_demo`を指定してください。時間統計は撮影全体の値なので、CSVの各フレーム行で同じ撮影・ROIの値が繰り返されます。

Pythonから元の配列を読む例：

```python
import numpy as np
with np.load('captures/撮影フォルダ/frame_0000/arrays.npz', allow_pickle=False) as data:
    depth = data['depth_z16']
    valid = data['valid_mask']
    xyz = data['xyz_m']
    print('欠損率:', 1 - valid.mean())
    print('有効点数:', valid.sum())
    points = xyz[valid]
```

点群のみでは、値0だった画素の位置が失われます。欠損研究には`depth_z16`、`valid_mask`、整理された`xyz_m`を併用してください。

## 接続・終了時の動作と確認状況

- Advanced Modeが無効の場合、接続時に有効化して再認識を待ちます。これはカメラを一度切断・再接続する動作になります。
- 接続後に記録した元のAdvanced Mode設定・露光・ゲイン・投光設定へ、正常終了時に復元を試みます。復元に失敗した場合は表示します。Advanced Mode自体は有効のままです。強制終了・電源断では復元できません。
- 起動時にハードウェアの範囲を問い合わせ、適用できない設定はエラーを表示します。設定変更が途中で失敗した場合は直前の設定へ復元を試みます。
- ベースのテストはカメラ非接続で実行できます：`python -m unittest discover -s tests -v`。
- Z16の完全保存、欠損とNaN、PLY座標、Depth単位整合、時間統計、未完了撮影の区別、距離設定の曖昧さの拒否、ボタン操作前に書き込まないことをテストします。
- 制作環境にはD435用Python SDKがなく、実カメラの接続・GUI操作・20cmでの測距・ノイズ低減効果は未検証です。カメラなしの取得／保存経路は合成デモを使ってテストしています。

## 実装時に確認した公式資料

- [D400 Advanced ModeのPython例](https://github.com/realsenseai/librealsense/blob/master/wrappers/python/examples/python-rs400-advanced-mode-example.py)
- [SDKの点群処理例](https://github.com/realsenseai/librealsense/blob/master/wrappers/python/examples/opencv_pointcloud_viewer.py)
- [撮影パラメータの調整](https://dev.realsenseai.com/docs/tuning-depth-cameras-for-best-performance/)
- [レーザープロジェクタとスペックル](https://dev.realsenseai.com/docs/projectors/)
