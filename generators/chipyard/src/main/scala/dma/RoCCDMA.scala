package chipyard.dma

import chisel3._
import chisel3.util._
import org.chipsalliance.cde.config.{Config, Parameters}
import org.chipsalliance.diplomacy.lazymodule.LazyModule
import freechips.rocketchip.diplomacy.IdRange
import freechips.rocketchip.subsystem.ExtMem
import freechips.rocketchip.tile._
import freechips.rocketchip.tilelink._

class RoCCDMA(val delay: Double = 0.0)(implicit p: Parameters) extends LazyRoCC(OpcodeSet.custom0) {
  val client = TLClientNode(Seq(TLMasterPortParameters.v1(Seq(
    TLMasterParameters.v1(name = "rocc-dma", sourceId = IdRange(0, 1))))))
  // Test-only random A/D delays use an existing adapter; no interconnect edits.
  if (delay > 0) tlNode := TLDelayer(delay) := client else tlNode := client
  override lazy val module = new RoCCDMAModule(this)
}
class RoCCDMAModule(outer: RoCCDMA)(implicit p: Parameters) extends LazyRoCCModuleImp(outer) {
  require(io.cmd.bits.rs1.getWidth == 64, "DMA ABI requires RV64")
  val region = p(ExtMem).get.master
  val controller = Module(new DMAController(region.base, region.size))
  controller.io.cmd.valid := io.cmd.valid
  io.cmd.ready := controller.io.cmd.ready
  controller.io.cmd.bits.funct := io.cmd.bits.inst.funct
  controller.io.cmd.bits.source := io.cmd.bits.rs1
  controller.io.cmd.bits.destination := io.cmd.bits.rs2
  controller.io.cmd.bits.rd := io.cmd.bits.inst.rd
  controller.io.cmd.bits.xd := io.cmd.bits.inst.xd
  controller.io.cmd.bits.xs1 := io.cmd.bits.inst.xs1
  controller.io.cmd.bits.xs2 := io.cmd.bits.inst.xs2
  io.resp.valid := controller.io.resp.valid
  controller.io.resp.ready := io.resp.ready
  io.resp.bits.rd := controller.io.resp.bits.rd
  io.resp.bits.data := controller.io.resp.bits.status
  io.busy := controller.io.busy
  io.interrupt := false.B
  // Reserved for a future L1 policy; version 1 uses only coherent TileLink.
  io.mem.req.valid := false.B
  io.mem.s1_kill := false.B
  io.mem.s2_kill := false.B
  io.fpu_req.valid := false.B
  io.fpu_resp.ready := true.B

  val (mem, edge) = outer.client.out.head
  val beatBytes = edge.bundle.dataBits / 8
  require(beatBytes >= 8 && isPow2(beatBytes))
  val request = controller.io.request
  val address = request.bits.address(edge.bundle.addressBits - 1, 0)
  // TileLink sub-beat reads return data in the addressed byte lanes.
  def shift(a: UInt): UInt = if (beatBytes == 8) 0.U else Cat(a(log2Ceil(beatBytes)-1,3), 0.U(6.W))
  val readShift = Reg(UInt(log2Ceil(beatBytes * 8).W))
  val get = edge.Get(0.U, address, 3.U)
  val put = edge.Put(0.U, address, 3.U, request.bits.data << shift(address))
  mem.a.valid := request.valid
  mem.a.bits := Mux(request.bits.write, put._2, get._2)
  request.ready := mem.a.ready
  when(mem.a.fire) {
    assert(Mux(request.bits.write, put._1, get._1))
    readShift := shift(address)
  }
  controller.io.reply.valid := mem.d.valid
  mem.d.ready := controller.io.reply.ready
  controller.io.reply.bits.data := mem.d.bits.data >> readShift
  controller.io.reply.bits.error := mem.d.bits.denied || mem.d.bits.corrupt
  when(mem.d.fire) {
    assert(mem.d.bits.source === 0.U)
    assert(mem.d.bits.size === 3.U)
    assert(mem.d.bits.opcode === TLMessages.AccessAck || mem.d.bits.opcode === TLMessages.AccessAckData)
  }
  mem.b.ready := true.B
  mem.c.valid := false.B
  mem.c.bits := DontCare
  mem.e.valid := false.B
  mem.e.bits := DontCare
}
class WithRoCCDMA(delay: Double = 0.0) extends Config((site, here, up) => {
  case BuildRoCC => up(BuildRoCC) :+ ((p: Parameters) => LazyModule(new RoCCDMA(delay)(p)))
})
