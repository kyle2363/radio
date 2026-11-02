package dev.radio.mobile

import dev.radio.mobile.portable.*
import org.junit.Assert.*
import org.junit.Test

class CatalogTransfersTest {
    private fun descriptor(index:Int,size:Int) = VectorDescriptor("$index","text","model","1",size/4,"ip","none","float32",size,index.toString(16).padStart(64,'0'))
    @Test fun `batch size counts actual wire bytes and request entries`() {
        val rows=(1..100).map{descriptor(it,262144)}
        val batches=CatalogTransfers.batches(rows)
        assertEquals(rows,batches.flatten())
        assertTrue(batches.size>1)
        assertTrue(batches.all{it.size<=64 && it.sumOf{row->row.byteLength.toLong()}<=8_000_000})
        assertTrue(CatalogTransfers.MAX_BYTES<80_000_000)
        assertEquals(listOf(64,36),CatalogTransfers.batches((1..100).map{descriptor(it,4)}).map{it.size})
        assertTrue(CatalogTransfers.batches(emptyList()).isEmpty())
    }
    @Test fun `oversized individual vectors cannot bypass batch ceiling`() {
        assertThrows(CatalogException::class.java){CatalogTransfers.batches(listOf(descriptor(1,8_000_004)))}
    }
}
