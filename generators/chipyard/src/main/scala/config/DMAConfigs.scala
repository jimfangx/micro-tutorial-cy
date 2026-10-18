package chipyard

import org.chipsalliance.cde.config.Config

class DMARocketConfig extends Config(
  new chipyard.dma.WithRoCCDMA ++
  new freechips.rocketchip.rocket.WithNHugeCores(1) ++
  new chipyard.config.AbstractConfig)
