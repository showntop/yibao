//! 划词/唤起条的纯几何工具（无 Tauri 依赖，可独立单测）。

/** 唤起条落位：光标右下偏移（14,18），越出所在屏右/下缘时翻转到左/上；纯函数便于单测。
 *  mon = (屏原点x, 屏原点y, 屏宽, 屏高)，全部逻辑坐标。 */
pub fn clamp_bar_pos(mx: f64, my: f64, bar_w: f64, bar_h: f64, mon: (f64, f64, f64, f64)) -> (f64, f64) {
    let (mx0, my0, mw, mh) = mon;
    let mut x = mx + 14.0;
    let mut y = my + 18.0;
    if x + bar_w > mx0 + mw {
        x = mx - bar_w - 14.0;
    }
    if y + bar_h > my0 + mh {
        y = my - bar_h - 18.0;
    }
    (x.max(mx0), y.max(my0))
}

/** 选区换算：overlay 窗口逻辑坐标 → 虚拟桌面物理像素（mss 坐标系）。
 *  mon_px_origin = 屏物理原点（可为负：副屏在主屏左侧）；scale = 屏 scale_factor。纯函数便于单测。 */
pub fn snip_abs_rect(r: (f64, f64, f64, f64), mon_px_origin: (i64, i64), scale: f64) -> (i64, i64, i64, i64) {
    let (l, t, w, h) = r;
    (
        mon_px_origin.0 + (l * scale).round() as i64,
        mon_px_origin.1 + (t * scale).round() as i64,
        (w * scale).round().max(1.0) as i64,
        (h * scale).round().max(1.0) as i64,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bar_pos_bottom_right_offset() {
        // 屏幕中央：光标右下偏移
        let (x, y) = clamp_bar_pos(500.0, 400.0, 328.0, 56.0, (0.0, 0.0, 1440.0, 900.0));
        assert_eq!((x, y), (514.0, 418.0));
    }

    #[test]
    fn bar_pos_flips_at_right_and_bottom_edges() {
        // 右边缘：翻到光标左侧；下边缘：翻到光标上方
        let (x, y) = clamp_bar_pos(1400.0, 880.0, 328.0, 56.0, (0.0, 0.0, 1440.0, 900.0));
        assert_eq!((x, y), (1400.0 - 328.0 - 14.0, 880.0 - 56.0 - 18.0));
    }

    #[test]
    fn bar_pos_respects_monitor_origin() {
        // 副屏（原点在 1440,0）：越界判断相对该屏
        let (x, y) = clamp_bar_pos(1500.0, 100.0, 328.0, 56.0, (1440.0, 0.0, 1440.0, 900.0));
        assert_eq!((x, y), (1514.0, 118.0));
    }

    #[test]
    fn snip_rect_scales_and_offsets() {
        // scale=2 retina：逻辑 (100,50,200,120) + 屏原点 (0,0) → 物理 (200,100,400,240)
        let r = snip_abs_rect((100.0, 50.0, 200.0, 120.0), (0, 0), 2.0);
        assert_eq!(r, (200, 100, 400, 240));
        // 副屏负原点（主屏左侧 1440 宽）：逻辑 (10,10,50,50) → 物理 (-1420+20, 20, 100, 100)
        let r2 = snip_abs_rect((10.0, 10.0, 50.0, 50.0), (-2880, 0), 2.0);
        assert_eq!(r2, (-2860, 20, 100, 100));
    }
}
