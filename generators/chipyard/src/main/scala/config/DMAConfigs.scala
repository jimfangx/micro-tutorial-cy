package chipyard

import org.chipsalliance.cde.config.Config

class DMARocketConfig extends Config(
  new chipyard.dma.WithRoCCDMA ++
  new freechips.rocketchip.rocket.WithNHugeCores(1) ++
  new chipyard.config.AbstractConfig)

// Same core and memory hierarchy; test-only stalls on accelerator A/D channels.
class DMAStallRocketConfig extends Config(
  new chipyard.dma.WithRoCCDMA(0.5) ++
  new freechips.rocketchip.rocket.WithNHugeCores(1) ++
  new chipyard.config.AbstractConfig)
