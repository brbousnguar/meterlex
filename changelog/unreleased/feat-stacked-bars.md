### Changed

- The reading over time is stacked by harness instead of painted with its leading one: a month of bars now shows the mix, in one order for the whole period, with a legend under the chart. Pointing at a part focuses that harness in every bar (worked out from the pointer's height, so a 1% band still responds); the legend focuses it too and pins it on a tap. Closes #20.
- The readout appears while the chart is being read and leaves with the pointer, instead of staying over the bars. On a phone it stays a panel above the plot.
- `scripts/check-bars.mjs` proves both: the readout never covers the column being pointed at, it flips at the right edge, it leaves with the pointer, and a part focuses its harness.
