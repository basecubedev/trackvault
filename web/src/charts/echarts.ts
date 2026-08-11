import * as echarts from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkPointComponent,
  TooltipComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

/**
 * The one place ECharts is assembled.
 *
 * Only the pieces this application draws are registered, so the bundle carries
 * two chart types rather than twenty. Importing the whole library from three
 * components would also be three places to notice when one of them stops being
 * used.
 */
echarts.use([
  BarChart,
  LineChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  MarkPointComponent,
  CanvasRenderer,
])

export { echarts }
export type EChartsOption = echarts.EChartsCoreOption
