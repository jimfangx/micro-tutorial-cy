package chipyard.dma

import chisel3._
import chiseltest._
import org.scalatest.flatspec.AnyFlatSpec

/** Exercise the real synthesized controller with adversarial memory timing.
  * Full-system tests separately cover the coherent TileLink adapter and Rocket.
  */
class DMAControllerSpec extends AnyFlatSpec with ChiselScalatestTester {
  behavior of "DMAController"
  it should "handle stalls, delayed replies, busy commands, errors, and reset" in {
    test(new DMAController(BigInt("80000000",16), BigInt("10000000",16)))
      .withAnnotations(Seq(VerilatorBackendAnnotation)) { c =>
      val base = BigInt("80000000",16)
      c.io.cmd.valid.poke(false.B)
      c.io.resp.ready.poke(false.B)
      c.io.request.ready.poke(false.B)
      c.io.reply.valid.poke(false.B)
      c.io.reply.bits.data.poke(0.U)
      c.io.reply.bits.error.poke(false.B)
      c.reset.poke(true.B); c.clock.step(3); c.reset.poke(false.B)
      c.io.busy.expect(false.B); c.io.request.valid.expect(false.B); c.io.resp.valid.expect(false.B)
      def cmd(fn: Int, s: BigInt, d: BigInt = 0, flags: Int = 6): Unit = {
        c.io.cmd.ready.expect(true.B)
        c.io.cmd.bits.funct.poke(fn.U)
        c.io.cmd.bits.source.poke(s.U); c.io.cmd.bits.destination.poke(d.U)
        c.io.cmd.bits.rd.poke(9.U)
        c.io.cmd.bits.xd.poke(((flags & 4) != 0).B)
        c.io.cmd.bits.xs1.poke(((flags & 2) != 0).B)
        c.io.cmd.bits.xs2.poke(((flags & 1) != 0).B)
        c.io.cmd.valid.poke(true.B); c.clock.step(); c.io.cmd.valid.poke(false.B)
      }
      def resp(status: Int): Unit = {
        c.io.resp.valid.expect(true.B); c.io.resp.bits.rd.expect(9.U)
        c.io.resp.bits.status.expect(status.U)
        c.io.request.valid.expect(false.B)
        c.clock.step(5) // response itself must tolerate backpressure
        c.io.resp.valid.expect(true.B); c.io.resp.bits.status.expect(status.U)
        c.io.resp.ready.poke(true.B); c.clock.step(); c.io.resp.ready.poke(false.B)
        c.io.busy.expect(false.B)
      }
      def config(s: BigInt = base, d: BigInt = base+4096): Unit = { cmd(0,s,d,7); resp(0) }
      def request(a: BigInt, write: Boolean, data: BigInt = 0): Unit = {
        c.io.request.valid.expect(true.B)
        c.io.request.bits.address.expect(a.U); c.io.request.bits.write.expect(write.B)
        if(write) c.io.request.bits.data.expect(data.U)
        c.io.cmd.valid.poke(true.B) // pending later command cannot be accepted
        for (_ <- 0 until 7) {
          c.io.cmd.ready.expect(false.B); c.io.resp.valid.expect(false.B)
          c.io.request.bits.address.expect(a.U); c.clock.step()
        }
        c.io.cmd.valid.poke(false.B)
        c.io.request.ready.poke(true.B); c.clock.step(); c.io.request.ready.poke(false.B)
        c.io.request.valid.expect(false.B)
      }
      def reply(data: BigInt = 0, error: Boolean = false): Unit = {
        for (_ <- 0 until 11) {
          c.io.resp.valid.expect(false.B); c.io.request.valid.expect(false.B); c.clock.step()
        }
        c.io.reply.ready.expect(true.B)
        c.io.reply.bits.data.poke(data.U); c.io.reply.bits.error.poke(error.B)
        c.io.reply.valid.poke(true.B); c.clock.step(); c.io.reply.valid.poke(false.B)
      }
      cmd(1,8); resp(2) // reset configuration invalid
      cmd(1,0); resp(0)
      cmd(127,0,0,4); resp(1)
      cmd(0,base,base+4096,6); resp(1) // malformed flags
      config()
      cmd(1,24)
      for(i <- 0 until 3) {
        val data = BigInt("fedcba9876543210",16) + i
        request(base+i*8,false); reply(data)
        request(base+4096+i*8,true,data); reply()
      }
      resp(0)
      cmd(1,9); resp(2)
      config(base+1); cmd(1,8); resp(2)
      config(base,base+8); cmd(1,16); resp(2)
      config(0); cmd(1,8); resp(2)
      config(base+BigInt("ffffff8",16)); cmd(1,16); resp(2)
      config((BigInt(1)<<64)-8); cmd(1,16); resp(2)
      config(); cmd(1,(BigInt(1)<<64)-8); resp(2)
      // A failed read must not generate a write.
      cmd(1,16); request(base,false); reply(error=true); resp(3)
      // A later failed write must stop, even after prior units have committed.
      cmd(1,24)
      request(base,false); reply(123)
      request(base+4096,true,123); reply()
      request(base+8,false); reply(456)
      request(base+4104,true,456); reply(error=true); resp(3)
      c.reset.poke(true.B); c.clock.step(3); c.reset.poke(false.B)
      c.io.busy.expect(false.B); c.io.request.valid.expect(false.B)
      cmd(1,8); resp(2)
    }
  }
}
