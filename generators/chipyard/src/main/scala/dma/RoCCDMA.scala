package chipyard.dma

import chisel3._
import chisel3.util._
import org.chipsalliance.cde.config.{Config, Parameters}
import org.chipsalliance.diplomacy.lazymodule.LazyModule
import freechips.rocketchip.tile._

class RoCCDMA(implicit p: Parameters) extends LazyRoCC(OpcodeSet.custom0) {
  override lazy val module = new RoCCDMAModule(this)
}
class RoCCDMAModule(outer: RoCCDMA)(implicit p: Parameters) extends LazyRoCCModuleImp(outer) {
  val pending = RegInit(false.B)
  val rd = Reg(UInt(5.W))
  val result = Reg(UInt(64.W))
  val source = Reg(UInt(64.W))
  val destination = Reg(UInt(64.W))
  io.cmd.ready := !pending
  io.busy := pending
  io.interrupt := false.B
  io.mem.req.valid := false.B
  io.mem.s1_kill := false.B
  io.mem.s2_kill := false.B
  io.fpu_req.valid := false.B
  io.fpu_resp.ready := true.B
  io.resp.valid := pending
  io.resp.bits.rd := rd
  io.resp.bits.data := result
  when(io.cmd.fire) {
    rd := io.cmd.bits.inst.rd
    pending := io.cmd.bits.inst.xd
    result := 1.U
    when(io.cmd.bits.inst.funct === 0.U) {
      source := io.cmd.bits.rs1
      destination := io.cmd.bits.rs2
      result := 0.U
    }
    // Bring-up only: COPY accepts zero bytes; replaced by the DMA datapath next.
    when(io.cmd.bits.inst.funct === 1.U) { result := Mux(io.cmd.bits.rs1 === 0.U, 0.U, 2.U) }
  }
  when(io.resp.fire) { pending := false.B }
}
class WithRoCCDMA extends Config((site, here, up) => {
  case BuildRoCC => up(BuildRoCC) :+ ((p: Parameters) => LazyModule(new RoCCDMA()(p)))
})
