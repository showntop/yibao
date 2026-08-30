/** 宿主器目录（design §3「器」）：重交互组件提升为宿主一等件的注册处。
 *
 * 提升标准（§11.3）：被 ≥2 个任务域需要的重交互组件才做文件级提升。
 * 今天编辑器只服务 zimeiti，所以以「面板型器」入册——地址可达（panel 引用）、
 * agent 可驱动（surface/editor.* tool）、宿主可编排（surface.open）；文件属主仍在插件。
 * 第二个任务域需要编辑器时，再把文件提升到宿主（届时插件改引宿主器）。
 */

export interface HostInstrument {
  id: string;
  /** 面板引用「插件:面板名」——器的物理载体 */
  panel: string;
  title: string;
  /** 器承载的域：对象模型的 doc_snapshot/selection 事件都挂在这个前缀下 */
  domain: string;
}

export const HOST_INSTRUMENTS: HostInstrument[] = [
  { id: "editor", panel: "zimeiti:editor", title: "写作编辑器", domain: "zimeiti" },
];

export function instrumentOf(panel: string): HostInstrument | undefined {
  return HOST_INSTRUMENTS.find((i) => i.panel === panel);
}
