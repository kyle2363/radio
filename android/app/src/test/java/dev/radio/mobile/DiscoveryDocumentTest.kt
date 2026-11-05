package dev.radio.mobile

import dev.radio.mobile.connection.ConnectionFailure
import dev.radio.mobile.connection.DiscoveryDocument
import dev.radio.mobile.connection.DiscoverySignedFields
import dev.radio.mobile.connection.validateDiscoveryFields
import dev.radio.mobile.connection.discoveryReadAllowed
import dev.radio.mobile.connection.discoveryHttpFailure
import dev.radio.mobile.connection.validateDiscoveryEndpoint
import org.junit.Assert.*
import org.junit.Test

class DiscoveryDocumentTest {
    private fun fields(server:String="s1", gen:Long=2, rev:Long=3, exp:Long=1100, endpoint:String="https://radio.example") = Pair(
        DiscoveryDocument(server,gen,rev,true,endpoint,990,exp,"payload","sig","key",java.time.Instant.ofEpochSecond(990).toString()),
        DiscoverySignedFields(server,gen,rev,true,endpoint,990,exp))
    @Test fun acceptsMatchingFreshSignedEnvelopeFields(){ val (d,p)=fields();assertEquals(3L,validateDiscoveryFields(d,p,"s1",1000).revision) }
    @Test fun rejectsWrongServer(){ val(d,p)=fields();assertThrows(ConnectionFailure::class.java){validateDiscoveryFields(d,p,"other",1000)} }
    @Test fun rejectsExpiredAndOverlongLease(){ val(d,p)=fields(exp=999);assertThrows(ConnectionFailure::class.java){validateDiscoveryFields(d,p,"s1",1000)};val(a,b)=fields(exp=2000);assertThrows(ConnectionFailure::class.java){validateDiscoveryFields(a,b,"s1",1000)} }
    @Test fun rejectsOuterMetadataThatDiffersFromSignedPayload(){ val(d,p)=fields(rev=4);assertThrows(ConnectionFailure::class.java){validateDiscoveryFields(d,p.copy(revision=3),"s1",1000)} }
    @Test fun throttlesResumeAndSessionReadsAndAllowsExplicitErrorRecovery(){assertFalse(discoveryReadAllowed(1059,1000,1000,false,false));assertTrue(discoveryReadAllowed(1060,1000,1000,false,false));assertFalse(discoveryReadAllowed(1014,1000,0,true,false));assertTrue(discoveryReadAllowed(1015,1000,0,true,false));assertFalse(discoveryReadAllowed(1060,0,1001,false,true))}
    @Test fun quotaHasSpecificMessageAndDiscoveryRequiresExactTunnelHost(){assertTrue(discoveryHttpFailure(429).safeMessage.contains("quota"));validateDiscoveryEndpoint("https://short-name.trycloudflare.com");assertThrows(ConnectionFailure::class.java){validateDiscoveryEndpoint("https://host.nested.trycloudflare.com")};assertThrows(ConnectionFailure::class.java){validateDiscoveryEndpoint("https://host.trycloudflare.com:443")}
    }
}
