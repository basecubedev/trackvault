import { useEffect, useRef } from 'react'
import type { HoverStore } from '../pages/TrackDetail/hover'
import { echarts, type EChartsOption } from './echarts'

/**
 * A chart, and nothing else.
 *
 * Every page builds its own option object and this only mounts it. Keeping the
 * library behind one component is what lets the option builders be tested as
 * plain functions -- no canvas, no layout, no browser -- which is where the
 * interesting decisions live anyway.
 *
 * It is also the only module that imports ECharts, which is what makes the
 * library a lazily loaded chunk: the track list draws no chart and never pays
 * for one.
 *
 * When a hover store is passed, the cursor is *coupled* to it in both
 * directions and neither direction re-renders anything. Moving the pointer over
 * the chart writes the sample to the store; the map's marker follows. Moving it
 * over the map writes there instead, and this shows the matching tooltip
 * through ECharts' own API.
 */
export function Chart({
  option,
  height = 320,
  hover,
  label,
}: {
  option: EChartsOption
  height?: number
  hover?: HoverStore
  label: string
}) {
  const container = useRef<HTMLDivElement | null>(null)
  const chart = useRef<echarts.ECharts | null>(null)
  const store = useRef(hover)
  store.current = hover

  useEffect(() => {
    if (!container.current) return
    const instance = echarts.init(container.current)
    chart.current = instance
    const resize = () => {
      instance.resize()
    }
    window.addEventListener('resize', resize)

    instance.getZr().on('mousemove', (event: { offsetX: number; offsetY: number }) => {
      const target = store.current
      if (!target) return
      const point = [event.offsetX, event.offsetY]
      if (!instance.containPixel('grid', point)) {
        target.set(null)
        return
      }
      const [index] = instance.convertFromPixel({ seriesIndex: 0 }, point)
      target.set(index === undefined ? null : Math.round(index))
    })
    instance.getZr().on('globalout', () => {
      store.current?.set(null)
    })

    return () => {
      window.removeEventListener('resize', resize)
      instance.dispose()
      chart.current = null
    }
  }, [])

  useEffect(() => {
    chart.current?.setOption(option, true)
  }, [option])

  // The other direction: what the map is pointing at, shown on the chart.
  // Imperative on purpose -- a render per pointer event is the cascade this
  // whole arrangement exists to avoid.
  useEffect(() => {
    if (!hover) return
    return hover.subscribe(() => {
      const instance = chart.current
      if (!instance) return
      const index = hover.read()
      if (index === null) {
        instance.dispatchAction({ type: 'hideTip' })
        return
      }
      instance.dispatchAction({ type: 'showTip', seriesIndex: 0, dataIndex: index })
    })
  }, [hover])

  return (
    <div
      ref={container}
      className="chart"
      style={{ height }}
      role="img"
      aria-label={label}
      data-testid="chart"
    />
  )
}

export default Chart
