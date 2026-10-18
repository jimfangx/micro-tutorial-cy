package chipyard.dma

import chisel3._
import chisel3.util._

class DMACommand extends Bundle {
  val funct = UInt(7.W)
  val source = UInt(64.W)
  val destination = UInt(64.W)
  val rd = UInt(5.W)
  val xd = Bool()
  val xs1 = Bool()
  val xs2 = Bool()
}
class DMAResponse extends Bundle {
  val rd = UInt(5.W)
  val status = UInt(64.W)
}
class DMARequest extends Bundle {
  val address = UInt(64.W)
  val write = Bool()
  val data = UInt(64.W)
}
class DMAMemoryResponse extends Bundle {
  val data = UInt(64.W)
  val error = Bool()
}

/** Command ABI and one-unit datapath. Memory adapter owns TileLink encoding. */
class DMAController(base: BigInt, size: BigInt) extends Module {
  require(base >= 0 && size > 0 && base + size < (BigInt(1) << 64))
  val io = IO(new Bundle {
    val cmd = Flipped(Decoupled(new DMACommand))
    val resp = Decoupled(new DMAResponse)
    val request = Decoupled(new DMARequest)
    val reply = Flipped(Decoupled(new DMAMemoryResponse))
    val busy = Output(Bool())
  })
  val idle :: read :: readWait :: write :: writeWait :: complete :: Nil = Enum(6)
  val state = RegInit(idle)
  val configured = RegInit(false.B)
  val srcConfig = Reg(UInt(64.W))
  val dstConfig = Reg(UInt(64.W))
  val src = Reg(UInt(64.W))
  val dst = Reg(UInt(64.W))
  val left = Reg(UInt(64.W))
  val buffer = Reg(UInt(64.W))
  val rd = Reg(UInt(5.W))
  val status = Reg(UInt(64.W))
  val respond = Reg(Bool())

  io.cmd.ready := state === idle
  io.busy := state =/= idle
  io.resp.valid := state === complete && respond
  io.resp.bits.rd := rd
  io.resp.bits.status := status
  io.request.valid := state === read || state === write
  io.request.bits.address := Mux(state === write, dst, src)
  io.request.bits.write := state === write
  io.request.bits.data := buffer
  io.reply.ready := state === readWait || state === writeWait

  // Widened sums reject overflow before narrowing to the physical TL address.
  val n = io.cmd.bits.source
  val srcEnd = srcConfig +& n
  val dstEnd = dstConfig +& n
  val validRange = srcConfig >= base.U && dstConfig >= base.U &&
    srcEnd <= (base + size).U && dstEnd <= (base + size).U
  val disjoint = srcEnd <= dstConfig || dstEnd <= srcConfig
  val aligned = (srcConfig(2,0) | dstConfig(2,0) | n(2,0)) === 0.U

  when(io.cmd.fire) {
    rd := io.cmd.bits.rd
    respond := io.cmd.bits.xd
    status := 1.U
    state := complete
    when(io.cmd.bits.funct === 0.U && io.cmd.bits.xd && io.cmd.bits.xs1 && io.cmd.bits.xs2) {
      srcConfig := io.cmd.bits.source
      dstConfig := io.cmd.bits.destination
      configured := true.B
      status := 0.U
    }
    when(io.cmd.bits.funct === 1.U && io.cmd.bits.xd && io.cmd.bits.xs1 && !io.cmd.bits.xs2) {
      status := 2.U
      when(n === 0.U) { status := 0.U }
      .elsewhen(configured && validRange && aligned && disjoint) {
        src := srcConfig
        dst := dstConfig
        left := n
        status := 0.U
        state := read
      }
    }
  }
  when(io.request.fire) {
    assert(left >= 8.U && left(2,0) === 0.U)
    state := Mux(state === read, readWait, writeWait)
  }
  when(io.reply.fire) {
    when(io.reply.bits.error) {
      // Even a denied write can leave earlier units committed: never CPU-retry.
      status := 3.U
      state := complete
    }.elsewhen(state === readWait) {
      buffer := io.reply.bits.data
      state := write
    }.otherwise {
      when(left === 8.U) { state := complete }
      .otherwise {
        src := src + 8.U
        dst := dst + 8.U
        left := left - 8.U
        state := read
      }
    }
  }
  when(state === complete && (!respond || io.resp.fire)) { state := idle }

  // A response cannot be consumed before a corresponding request; only one
  // memory transaction is outstanding, including when either channel stalls.
  val outstanding = RegInit(false.B)
  when(io.request.fire) { assert(!outstanding); outstanding := true.B }
  when(io.reply.fire) { assert(outstanding); outstanding := false.B }
  when(state === complete) { assert(!outstanding) }
  assert(!(io.request.valid && io.reply.ready))
  val stalled = RegNext(io.request.valid && !io.request.ready, false.B)
  val previous = RegNext(io.request.bits.asUInt)
  when(stalled) { assert(io.request.valid && io.request.bits.asUInt === previous) }
}
