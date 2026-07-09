# Holographic Session Links: A Post-Quantum Communication Protocol Based on Algebraic Session Resonance

**Maciej Mazur** — Independent AI Researcher | Warsaw, Poland
GitHub: Maciej-EriAmo/HolonOS
Version: 1.2.0 | Date: 2026-07-09 | License: CC BY 4.0

---

## Abstract

We propose **Holographic Session Links (HSL)** — a post-quantum communication protocol in which network routing, access control, and message authentication emerge from the algebraic resonance between communicating nodes' session-state polynomials, rather than from external certificates, address spaces, or permission systems.

In HSL, a message from node $A$ to node $B$ is decryptable **if and only if** node $B$'s session polynomial $s_B$ is algebraically resonant with the sender's session polynomial $s_A$. If resonance fails, the payload does not trigger an "access denied" response — it **collapses into thermodynamic noise indistinguishable from the Ring-LWE error distribution**. No routing table, no certificate authority, and no firewall rule can prevent or simulate this collapse.

The protocol extends **Holographic Session Spaces (HSS)** [Mazur, 2026a] from single-node process isolation to multi-node networks. Security is not a layer applied to communication — it is a topological property of the communication space itself.

**Central thesis.** A node exists only within the space defined by its secret-dependent session projection $\Phi$. Communication between nodes is possible only when their projection spaces share algebraic resonance. All transmissions outside resonant space are computationally indistinguishable from random samples in $R_q$.

**Keywords:** Ring-LWE, post-quantum networking, session resonance, capability protocol, PrismMask, HolonOS, multi-agent AI security, hybrid QKD, information topology

**Prior art (DOI):**
- Holon Architecture: 10.5281/zenodo.19371554
- HolonFS: 10.5281/zenodo.19366419
- Prismatic Attention: 10.5281/zenodo.19371560
- Harmonic Attention: 10.5281/zenodo.19387523
- HSS: 10.5281/zenodo.19548693

---

## 1. Introduction

### 1.1 The Structural Problem in Current Internet Architecture

The internet defined by TCP/IP is a network of addresses. Security is external to the protocol — applied as a layer above routing (TLS, firewalls, ACLs). Trust is delegated to third parties (Certificate Authorities, DNS, PKI). The result is a system where:

- A node can receive any message directed to its address
- Authentication verifies *who sent* the message, not *whether the receiver is entitled to perceive it*
- Compromise of infrastructure (CA, DNS) breaks the entire trust model
- Security scales as $O(n^2)$ policy rules for $n$ communicating parties

This architecture has a deeper structural property: **security is a barrier, not a geometry.** Barriers can be bypassed at the layer below them; the geometry of the communication space cannot.

### 1.2 The HSL Paradigm

HSL defines a network as a space of session states. A node is not an address — it is a session polynomial $s \in R_q$ derived from its private $\Phi$ geometry. Communication is not packet transmission — it is **algebraic resonance between session states**.

The protocol operates on a single principle:

> A message encrypted for resonance with $s_A$ can be decrypted only by a node possessing $s_A$. All other nodes receive computational noise. No routing decision, no firewall rule, no certificate can change this.

This is not a security policy. It is a consequence of the Decision-RLWE hardness assumption.

### 1.3 Relationship to HSS

**HSS** [Mazur, 2026a] applies this principle *within* a single node — isolating processes from each other using capability tokens derived from $\Phi$.

**HSL** extends this principle *between* nodes — creating a network in which inter-node communication inherits the same algebraic isolation guarantees as intra-node process isolation.

The result is a unified security model from the kernel process table to the wide-area network, based on a single mathematical foundation.

### 1.4 Relationship to Existing Post-Quantum Standards

NIST PQC standardization (CRYSTALS-Kyber, based on Module-LWE) focuses on replacing classical key exchange and digital signatures. HSL uses Ring-LWE not as a drop-in replacement for RSA — but as the **organizing principle of the communication topology itself**.

This is a different deployment of lattice cryptography, complementary to (rather than competing with) Kyber-style key encapsulation.

### 1.5 Relationship to Quantum Networks (QKD)

Quantum Key Distribution provides a shared symmetric key between two endpoints with information-theoretic security but does not natively provide routing, identity, authorization, session semantics, or multi-party isolation. HSL operates on top of any high-entropy seed and therefore composes naturally with QKD: a QKD-distributed key serves as a seed for the HSL session secret, with HSL providing the application-layer functions QKD does not. We discuss this hybrid deployment in §6.4.

---

## 2. Formal Foundations

### 2.1 The Session State Polynomial

Let $R_q = \mathbb{Z}_q[X]/(X^N + 1)$ be the polynomial quotient ring, where $N$ is a power of 2 and $q$ is a prime with $q \equiv 1 \pmod{2N}$.

**Definition 2.1 (Session State).** A node's session state is a triple:

$$\Phi = (s_{\text{sess}},\; W_{\text{proj}},\; \text{epoch})$$

where:
- $s_{\text{sess}} \in R_q$ — the session secret polynomial, with small coefficients sampled from $\chi_s$
- $W_{\text{proj}} \in \mathbb{R}^{d \times N}$ — the projection matrix frozen at session birth
- $\text{epoch} = \lfloor t / T_{\text{epoch}} \rfloor$ — the temporal epoch index

The session secret is derived as:

$$s_{\text{sess}} = \text{KDF}(\Phi^2,\; \text{CSPRNG},\; \text{epoch})$$

where $\Phi^2$ is the long-term identity component of the node.

**Note on $\Phi^2$.** In the Holon architecture, $\Phi^2$ is the identity-layer embedding with half-life $T_{1/2} = 720\text{h}$ [Mazur, 2026b]. For the purposes of this paper, $\Phi^2$ may be replaced with any long-term identity secret derived through a KDF-suitable source (e.g., a TPM-rooted device key, a QKD-distributed seed, or an HSM-stored master secret). The Holon-specific construction is not load-bearing for HSL's security analysis; HSL requires only that $\Phi^2$ has sufficient min-entropy and is not exposed outside the node. See Appendix A for a self-contained construction.

**Definition 2.2 (Session Key Pair).** From $s_{\text{sess}}$, the node generates its public identifier $(a, b)$:

$$a \xleftarrow{\$} R_q, \quad b = a \cdot s_{\text{sess}} + e \pmod{q}$$

where $e \xleftarrow{\chi_e} R_q$ is a fresh random error polynomial. The pair $(a, b)$ is the node's **session public key** for this epoch.

### 2.2 HSL Message Encryption

**Definition 2.3 (HSL Ciphertext).** To transmit payload $m \in \{0,1\}^N$ from node $A$ to node $B$, the sender computes:

$$u = a_B \cdot r + e_1 \pmod{q}$$
$$v = b_B \cdot r + e_2 + \lfloor q/2 \rceil \cdot m \pmod{q}$$

where $r, e_1, e_2 \xleftarrow{\chi_r} R_q$ are fresh ephemeral polynomials sampled per message.

The HSL packet is $(u, v, \text{AAD}, \text{nonce}, \text{MAC})$, where AAD encodes the session context (Section 3).

### 2.3 The Resonance Condition

**Theorem 2.4 (Session Resonance).** Let node $B$ attempt decryption with polynomial $s'$:

$$m' = v - u \cdot s' \pmod{q}$$

Substituting:

$$m' = \lfloor q/2 \rceil \cdot m + \underbrace{(e \cdot r + e_2 - e_1 \cdot s')}_{\text{bounded noise}} + \underbrace{a_B \cdot r \cdot (s_B - s')}_{\text{resonance term}} \pmod{q}$$

**Case 1 — Perfect resonance** ($s' = s_B$):

The resonance term $a_B \cdot r \cdot (s_B - s') = 0$ vanishes. The bounded noise $(e \cdot r + e_2 - e_1 \cdot s_B)$ satisfies $\|\text{noise}\|_\infty \ll q/4$ under standard parameter choices. Rounding recovers $m$ exactly:

$$m = \text{Round}\!\left(\frac{m'}{q/2}\right) \bmod 2$$

**Case 2 — Resonance failure** ($s' \neq s_B$):

The resonance term $a_B \cdot r \cdot (s_B - s')$ does not vanish. Since $a_B$ is uniform in $R_q$ and $r$ is a fresh ephemeral polynomial, their product with the nonzero difference $(s_B - s')$ is computationally indistinguishable from uniform in $R_q$ under the Decision-RLWE assumption.

The recovered value $m'$ is therefore **uniformly random** — bounded noise plus a uniform mask, with no recoverable signal.

**Corollary 2.5 (Computational Zero).** Under Decision-RLWE hardness, no polynomial-time algorithm can distinguish a failed decryption from a fresh sample from the uniform distribution over $R_q$. The payload does not merely fail to decode — it ceases to be informationally distinguishable for the non-resonant receiver.

---

## 3. Session Context and PrismMasks

### 3.1 Holographic Session Links Protocol

A **Holographic Session Link** is a directed communication channel from node $A$ to node $B$, authenticated by the session states of both endpoints.

**Definition 3.1 (HSL Session).** An HSL session is a tuple:

$$\mathcal{L}_{AB} = (s_A,\; s_B,\; \text{AAD}_{AB},\; \text{epoch})$$

The session is established when both nodes derive compatible session polynomials through shared context, without any trusted third party (or, in hybrid mode, using a QKD-derived seed; see §6.4).

### 3.2 Context-Bound Derivation (PrismMasks)

For targeted communication — where node $A$ wishes to reach a specific session configuration of node $B$ — HSL employs **PrismMasks** based on KDF derivation.

**Definition 3.2 (PrismMask).** Let $\mathcal{P}$ be the set of authorized prisms for a given task. The targeted session polynomial is:

$$s_{\text{target}} = \text{KDF}\!\left(s_{\text{sess}},\; \text{JSON}\!\left(\{\text{"task"}: t,\; \text{"prisms"}: \mathcal{P}\}\right)\right)$$

The context is bound as Additional Authenticated Data:

$$\text{AAD} = H\!\left(\text{Source\_ID} \parallel \text{Target\_ID} \parallel \text{Task\_ID} \parallel \text{nonce}\right)$$

**Theorem 3.3 (Context Binding).** Any modification to $\text{AAD}$ — including substitution of Target\_ID or Task\_ID — produces a completely uncorrelated $s'_{\text{target}}$, maximizing the resonance term $a \cdot r \cdot (s_{\text{target}} - s'_{\text{target}})$ and collapsing the payload to noise.

This is the HSL analog of authenticated encryption: not only is the *content* protected, but the *context of the communication* is algebraically sealed.

### 3.3 Capability Tokens

**Definition 3.4 (Capability Token).** A node's capability to access prism $p$ is:

$$\text{cap}(p) = \text{HMAC}(s_{\text{target}},\; p)$$

The receiving node verifies: recompute $\text{cap}'(p) = \text{HMAC}(s'_{\text{target}}, p)$ and check $\text{cap}'(p) \stackrel{?}{=} \text{cap}(p)$.

This verification requires no lookup table, no ACL, no central authority. Access is an **algebraic property of the session state** — not a permission granted by an external system.

### 3.4 Epoch Rotation and Forward Secrecy

Session polynomials rotate with each epoch:

$$s_{\text{sess}}^{(e)} = \text{HMAC}(s_{\text{base}},\; e), \quad e = \lfloor t / T_{\text{epoch}} \rfloor$$

**Theorem 3.5 (Inter-Epoch Forward Secrecy).** Compromise of $s_{\text{sess}}^{(e)}$ reveals no information about $s_{\text{sess}}^{(e')}$ for $e' < e$, under HMAC-SHA256 pseudorandomness. Messages encrypted in previous epochs remain secure.

**Limitation (per-message PFS).** Within an epoch, HSL v1.1 does not provide per-message forward secrecy: compromise of $s_{\text{sess}}^{(e)}$ reveals all messages encrypted under it during epoch $e$. A Double-Ratchet construction analogous to the Signal Protocol [Cohn-Gordon et al., 2020] is being developed and is listed in §9.5. For deployments that require per-message PFS today, $T_{\text{epoch}}$ should be tightened (e.g., 1–5 minutes), trading key-management overhead for finer-grained secrecy windows.

---

## 4. Network Topology as Session Geometry

### 4.1 The HSL Network Model

**Definition 4.1 (HSL Network).** An HSL network is a directed graph $G = (V, E)$ where:

- Each vertex $v \in V$ is a node with session state $\Phi_v$
- Each edge $(u, v) \in E$ represents an HSL session $\mathcal{L}_{uv}$
- Edge existence requires algebraic resonance — it is not administratively assigned

The network topology emerges from the distribution of session states. There is no global routing table. A message propagates along resonant edges until it reaches its target — or collapses.

### 4.2 Comparison with Existing Network Models

| Property | TCP/IP | TLS/PKI | HSL |
|---|---|---|---|
| **Trust model** | Address-based | CA-delegated | Algebraic resonance |
| **Routing** | Address table | Address + cert | Session state |
| **Access denied** | Firewall rule | Certificate rejection | Computational collapse |
| **Third party required** | DNS | CA | None (or QKD) |
| **Post-quantum** | No | Partially (TLS 1.3+) | Yes (Ring-LWE) |
| **Security location** | External layer | External layer | Topological property |
| **Compromise blast radius** | Network-wide | CA-wide | Single session |

### 4.3 Computational Interpretation

In HSL, message delivery is not a routing decision — it is a computational event.

A resonant message carries information. A non-resonant message carries entropy. The receiving node cannot distinguish between a message addressed to another node and random noise — because under RLWE hardness, they are computationally identical.

This gives HSL a property no classical protocol achieves: **the act of eavesdropping is indistinguishable from receiving random noise**. An adversary cannot determine whether a channel exists, whether a message was sent, or whether a node is participating in the network — without possessing the correct session state.

### 4.4 Vacuum Decay as Network Garbage Collection

When a node's session ends (state revoked), all HSL sessions involving that node become unreachable. The session polynomials $s_A$ are deleted from the node's keyring.

Any cached messages or session state for the revoked node is now encrypted under a key that no longer exists. The **Free Energy Principle** detector (§5 of HSS [Mazur, 2026a]) classifies these as vacuum candidates — high entropy, no structure, no predictive signal.

The network self-cleans: orphaned sessions are annihilated by the FEP-driven garbage collector without any administrative intervention.

---

## 5. Security Analysis

### 5.1 Threat Model

We assume an adversary who can:

- Observe all network traffic (passive eavesdropper)
- Inject arbitrary packets (active attacker)
- Compromise individual nodes (but not the Ring-LWE problem)
- Control network infrastructure (routers, DNS, BGP)

The adversary **cannot**:

- Solve Decision-RLWE in polynomial time
- Access the session state $\Phi$ of uncompromised nodes
- Forge capability tokens without $s_{\text{target}}$

### 5.2 Security Properties

**Confidentiality.** IND-CPA security under Ring-LWE hardness. A passive adversary observing $(u, v)$ cannot recover $m$ without $s_B$.

**Authentication.** Context binding through AAD. A message modified in transit produces mismatched AAD, causing resonance failure and payload collapse.

**Non-routability.** An adversary capturing a packet cannot redirect it to a different node — the ciphertext is bound to $s_B$ algebraically, not by address.

**Unobservability.** Under RLWE, non-resonant packets are indistinguishable from uniform samples. Network participation is invisible to non-resonant observers.

**Post-quantum security.** Ring-LWE is believed hard for quantum computers. Shor's algorithm does not apply to lattice problems. Grover's algorithm reduces symmetric security by a factor of $\sqrt{2}$, leaving 128-bit security with $q \approx 3329, N = 256$ (Kyber parameters).

### 5.3 Side-Channel Mitigation: Blinded Variance

To prevent oracle attacks — where an adversary probes the network to map session geometry through timing or error responses — HSL implements **blinded variance evaluation**.

Before any variance check on decrypted output $m'$, the daemon injects calibrated noise:

$$\text{Var}_{\text{eval}} = \text{Var}(m' + \eta), \quad \eta \sim \mathcal{N}(0, \sigma_\xi^2)$$

where $\sigma_\xi = 0.05\theta$ and $\theta$ is the decision threshold. This ensures that partial resonance — where an attacker has approximate knowledge of $s_B$ — yields statistically indistinguishable responses from total computational noise.

**Rate-limit scope.** The blinding limit is **100 variance evaluations per second per (node, link) pair**. In multi-link deployments — for example, an orchestrator hub serving many agent peers — each link maintains its own counter; aggregate evaluation across the orchestrator is the sum of per-link allowances. This prevents correlation attacks across multiple links from reconstructing partial-resonance fingerprints, while permitting realistic throughput on individual sessions.

---

## 6. Deployment Architecture

### 6.1 Multi-Agent AI Networks (Primary Target)

HSL was designed with multi-agent AI systems as the primary deployment target. In current agent frameworks (LangChain, AutoGPT, CrewAI, Microsoft AutoGen), agents share a flat namespace. A compromised agent can access data belonging to any other agent. There is no mathematical isolation — only software convention.

In an HSL-networked multi-agent system:

- Each agent has a session state $\Phi_{\text{agent}}$ derived from the orchestrating $\Phi_{\text{system}}$
- Inter-agent communication requires algebraic resonance
- An agent cannot receive data outside its prism — not because of a policy rule, but because non-resonant messages collapse to noise before reaching the agent's perception
- Compromise of one agent reveals nothing about other agents' session states under RLWE hardness

To our knowledge, this is the first cryptographic perceptual-isolation framework for multi-agent AI systems deployable on existing infrastructure.

### 6.2 HSL Without Infrastructure Changes

HSL can be deployed on existing Linux infrastructure without kernel modifications:

**FUSE overlay.** `holon-fuse` intercepts filesystem access, replacing path-based routing with session-state routing. Zero changes to applications or kernel.

**Network proxy sidecar.** In Kubernetes environments, an HSL-aware sidecar intercepts all inter-pod communication, wrapping TCP packets in HSL ciphertexts. Existing applications see standard TCP — the cryptographic isolation is transparent.

**Replace TLS.** For applications that control their network stack, HSL session establishment replaces the TLS handshake. No CA required. Session establishment is $O(1)$ polynomial operations.

### 6.3 Kernel Integration (HolonOS Runtime)

For full OS-level enforcement, the HSL daemon integrates with the Linux kernel through:

- **LSM hook** (`security/holo/holo_lsm.c`) — intercepts all filesystem access, validates capability tokens
- **Kernel keyring** — stores `phi_session_id` and `capability_hint` per process
- **Netlink socket** — bidirectional communication between kernel and HSL daemon for cache invalidation

The kernel module is a **pure relay** — it carries no cryptographic logic. All Ring-LWE operations occur in the privileged userspace daemon. No plaintext ever enters kernel memory.

### 6.4 Hybrid QKD + HSL Deployment

Quantum Key Distribution distributes a shared symmetric key between two endpoints with information-theoretic security but provides no native concept of routing, authorization, identity, or session semantics. HSL is naturally complementary in a hybrid stack:

1. A QKD link distributes a high-entropy seed $k_{\text{QKD}}$ between nodes $A$ and $B$.
2. Both nodes derive session secrets via $s_{\text{sess}} = \text{KDF}(k_{\text{QKD}},\; \Phi^2_{\text{node}},\; \text{epoch})$.
3. HSL operates on the resulting session state, providing routing-by-resonance, capability tokens, and inter-epoch forward secrecy on classical hardware.

In this configuration, the security of the seed inherits QKD's information-theoretic guarantees; routing and access control inherit HSL's RLWE-based properties. Application layers and multi-agent AI workloads run unchanged on top.

This deployment model targets metropolitan quantum testbeds — for instance, the Deutsche Telekom / Qunnect Berlin testbed [Borowska et al., 2026] and Paderborn-led entanglement-distribution work [Becher et al., 2025]. HSL provides the application-layer functions QKD does not.

---

## 7. Implementation Status

| Component | Language | Status |
|---|---|---|
| **Cynober DB** (`karmazyn_hsl.py`, `cynober_rpc.py`) | Python | ✅ v7.7 — HSL-1.1 E2E over TCP; **294** automated tests; PyPI [`cynober-db`](https://pypi.org/project/cynober-db/) |
| `karmazyn_hss.py` + `karmazyn_hss_ntt.py` | Python | ✅ HSS v2.5 profiles + negacyclic NTT (`proto` / `standard` / `production`) |
| `karmazyn_qkd.py` | Python | ✅ QKD seed adapter (env / file / pipe) for hybrid §6.4 |
| `hss_demo.py` (HolonOS) | Python | ✅ v2.9 — 20/20 tests |
| `holonP.py` | Python | ✅ v5.11 — production |
| `holon_fs.py` | Python | ✅ production |
| `holo_lsm.c` (reference) | C | ✅ v3.4 — HSS aligned |
| `holo_lsm_ent.c` (enterprise) | C | ✅ v4.4 — hardened |
| Android/Kotlin port | Kotlin | 📅 Q2 2026 |
| FUSE deployment | C | 📅 Q3 2026 |
| HSL kernel daemon (HolonOS) | C | 📅 Q3 2026 |

**Primary reference implementation.** [Cynober DB](https://github.com/Maciej-EriAmo/DBase) deploys HSL as the sole transport for a team-scale database: one wire protocol (**Cynober-Secure-1.2** = HSS KEM handshake + HSL session tunnel + KarminQL RPC). No parallel TLS/HTTP/ODBC layer. Clients run on ARM64 (Termux, Samsung A54) and LAN desktops; typical footprint ~30 MB RAM, zero GPU dependency. See **Appendix B** for protocol-level detail.

---

## 8. Conceptual Motivation (Informal)

> *Note. This section sketches the conceptual analogies that informed HSL's design philosophy. They are inspirations, not formal equivalences. The security guarantees stated in §2–§5 do not depend on any of the analogies in this section.*

### 8.1 Inspiration from the Holographic Principle

The holographic principle [Susskind, 1995; 't Hooft, 1993] in theoretical physics states that the information content of a volume of space is bounded by the area of its boundary, suggesting that three-dimensional reality may be encoded on a two-dimensional information structure.

The Holon $\Phi$ matrix exhibits a hierarchical decay structure that we found suggestive of this layering principle:

- $\Phi^2$ (identity, $T_{1/2} = 720\text{h}$) — long-term, semi-permanent facts
- $\Phi^1$ (mid layer, $T_{1/2} = 168\text{h}$) — recurring patterns
- $\Phi^0$ (interior, $T_{1/2} = 24\text{h}$) — ephemeral context

Longer-lived information sits at the higher (more "surface-like") layer, paralleling the architectural framing of the holographic principle. We use this as design inspiration only; we do not claim mathematical equivalence between Bekenstein–Hawking entropy bounds and Holon's exponential decay schedule.

### 8.2 Session Revocation as a Causal Boundary

A black hole's event horizon defines a region from which information cannot reach external observers; in the holographic interpretation [Hawking, 1975; Susskind, 1995], that information is preserved on the boundary surface.

In HSL, when a session secret $s_A$ is revoked, the keyring entry is deleted. Cached ciphertexts encrypted under $s_A$ remain on storage media but are computationally inaccessible to any observer lacking $s_A$. Under Decision-RLWE hardness, recovery is conjectured to require effort comparable to solving a problem believed hard for quantum computers as well as classical ones.

The two structures — gravitational and computational — are formally distinct: one is a statement about spacetime causality, the other about complexity-theoretic intractability. They share only the architectural pattern of a boundary defined by inaccessibility, which informed the way we describe session revocation.

### 8.3 Resonance as Perceptual Asymmetry

Wheeler's *it from bit* [Wheeler, 1989] suggests that physical reality emerges from informational processes; quantum measurement collapses superposition into definite observable states.

In HSL, the message space looks uniform over $R_q$ until decrypted with the matching $s_B$, at which point the payload "clicks" into the intended message. We do not claim this is the projection postulate of quantum mechanics — the projection $W_{\text{proj}} \in \mathbb{R}^{d \times N}$ here is a classical linear map, not a Hilbert-space projector with the Born rule. The framing captures a *perceptual asymmetry* the protocol enforces: only the resonant observer sees structure; everyone else sees noise.

---

## 9. Future Work

1. **NTT-based Ring-LWE** — ✅ *partial (2026-07):* negacyclic NTT in `karmazyn_hss_ntt.py` for `standard` ($N{=}128$, $Q{=}3329$) and `production` ($N{=}512$, $Q{=}12289$) HSS profiles; `proto` ($N{=}15$) remains coefficient-domain. Constant-time hardening and $N{=}256$ Kyber-class profile remain open.
2. **HSL routing protocol** — ✅ *partial:* epoch rotation (`KARM_HSL_EPOCH_SEC`, default 3600s) and `qkd_fp` link binding in Cynober-Secure-1.2; formal mid-route epoch transition spec still open.
3. **Quantum Random Number Generator** — seed `base_secret` from true quantum entropy (e.g., ANU QRNG) for higher long-term entropy assurance.
4. **Formal security proof** — explicit reduction from Decision-RLWE to HSL session indistinguishability.
5. **Double Ratchet for sessions** — per-message forward secrecy within an epoch, analogous to the Signal Protocol.
6. **Cross-node Vacuum Decay** — distributed FEP garbage collection across HSL networks; ✅ *partial:* phi-space gossip (`cynober_gossip.py`, v7.7) over existing RPC tunnel.
7. **Hybrid QKD/HSL field demonstrator** — ✅ *partial:* `karmazyn_qkd.py` adapter slot (env / file / pipe) wired into `hybrid_link_seed()`; metropolitan hardware QKD link (e.g., DT Berlin) still open.

---

## 10. Conclusions

We have presented **Holographic Session Links** — a communication protocol in which network topology, access control, and message authentication emerge from algebraic resonance between communicating nodes' session states.

The central contribution is a paradigm shift: **security is not a layer applied to communication — it is the topology of the communication space itself.**

In HSL:

- Nodes are session states, not addresses
- Routing is resonance, not table lookup
- Access denied is computational collapse, not a firewall rule
- Trust requires no third party — it is an algebraic relationship; in hybrid mode it can be bootstrapped by a QKD-distributed seed.

TCP/IP defined the internet as a network of addresses.
HTTP defined the internet as a network of documents.
**HSL defines the internet as a network of session states.**

---

## Appendix A — Self-contained $\Phi^2$ Construction

For deployments outside the Holon ecosystem, $\Phi^2$ can be constructed as follows:

1. **Seed.** Sample $k_{\text{long}} \in \{0,1\}^{256}$ from a hardware RNG (TPM, HSM, or QRNG). Persist in non-exportable storage.
2. **Identity expansion.** $\Phi^2 = \text{HKDF}(k_{\text{long}},\; \text{node\_id} \parallel \text{deployment\_salt},\; 256\text{ bytes})$.
3. **Refresh.** $\Phi^2$ is rotated on operator-defined intervals (recommended $\geq 720\text{h}$); rotation produces $\Phi^2_{\text{new}}$ via re-derivation under a new deployment salt.

This construction satisfies HSL's requirements: (i) min-entropy $\geq 256$ bits; (ii) non-exportability; (iii) deterministic re-derivability for crash-recovery scenarios. Hybrid mode (§6.4) replaces step 1 with a QKD-distributed seed.

---

## Appendix B — Cynober DB Reference Implementation (v7.7)

*Informative. Describes the production HSL stack in [DBase](https://github.com/Maciej-EriAmo/DBase); not a normative protocol delta.*

### B.1 Stack

| Layer | Module | Role |
|---|---|---|
| Transport | `cynober_server.py` / `cynober_client.py` | TCP listener; SDK `connect()` / context manager |
| Handshake | `karmazyn_handshake.py` + `karmazyn_hss.py` | Ring-LWE KEM; profile negotiation via `hss_profile` field |
| Session | `karmazyn_hsl.py` | $\Phi^2$ node identity, link resonance, PrismMask/AAD, epoch rotation |
| Hybrid seed | `karmazyn_qkd.py` | Optional QKD slot → `hybrid_link_seed()` (§6.4) |
| Application | `cynober_rpc.py` | JSON-RPC over HSL ciphertext; capability token `cap` for `rpc:query` |

Wire version: **Cynober-Secure-1.2**. Handshake advertises `hss_profile` (`proto` \| `standard` \| `production`); mismatch aborts before RPC.

### B.2 HSS Profiles (aligned with HSS Paper v2.5)

| Profile | $N$ | $Q$ | Deployment |
|---|---|---|---|
| `proto` | 15 | 256 | Termux / dev; backward-compatible default |
| `standard` | 128 | 3329 | Team LAN; Kyber-class modulus |
| `production` | 512 | 12289 | Server deployments; NTT enabled when $q \equiv 1 \pmod{2N}$ |

Selection: `KARM_HSS_PROFILE` or `~/.karmazyn_client.json` → `hss_profile`. NTT: auto for `standard`/`production` unless `KARM_HSS_USE_NTT=0`.

### B.3 HSL Session Features

- **$\Phi^2$ identity:** persisted at `~/.karmazyn_phi2`; `commit` in `hsl_link` frame (hash only, no plaintext export).
- **Epoch rotation:** `s_target = KDF(link_seed, epoch)` with `epoch = ⌊t / T_epoch⌋`; `KARM_HSL_EPOCH_SEC` (default 3600).
- **Capability tokens:** `cap = capability_token(s_target, task, prisms)`; RPC requires resonance with `rpc:query` (v7.5+).
- **QKD fingerprint:** optional `qkd_fp` in link frame detects seed mismatch before query execution.
- **PSK overlay:** `KARM_PSK` mixed into link seed for closed networks.

### B.4 Verification

294 automated tests (`python -m unittest discover -s tests`) cover HSL sessions, HSS handshake across all profiles, NTT KEM, QKD adapter, RPC+capability, world replication, and gossip phi export. Package published as **`cynober-db` 7.7.0** on PyPI.

---

## References

1. Mazur, M. (2026a). *Holographic Session Spaces*. Zenodo. DOI: 10.5281/zenodo.19548693
2. Mazur, M. (2026b). *Holon: Holographic Cognitive Architecture*. Zenodo. DOI: 10.5281/zenodo.19371554
3. Mazur, M. (2026c). *HolonFS: Semantic Filesystem*. Zenodo. DOI: 10.5281/zenodo.19366419
4. Mazur, M. (2026d). *Prismatic Attention*. Zenodo. DOI: 10.5281/zenodo.19371560
5. Mazur, M. (2026e). *Harmonic Attention*. Zenodo. DOI: 10.5281/zenodo.19387523
6. Lyubashevsky, V., Peikert, C., & Regev, O. (2013). On ideal lattices and learning with errors over rings. *Journal of the ACM*, 60(6).
7. Avanzi, R. et al. (2021). *CRYSTALS-Kyber (version 3.02)*. NIST PQC submission.
8. Cohn-Gordon, K., Cremers, C., Dowling, B., Garratt, L., & Stebila, D. (2020). A formal security analysis of the Signal messaging protocol. *Journal of Cryptology*, 33.
9. Borowska, Z. A., Andrewski, S., De Pascalis, G., et al. (2026). *Bichromatic Quantum Teleportation of Weak Coherent Polarization States on a Metropolitan Fiber*. arXiv:2602.16613.
10. Becher, C. et al. (2025). *High-Fidelity Quantum Entanglement Distribution in Metropolitan Fiber Networks with Co-propagating Classical Traffic*. arXiv:2504.08927.
11. Susskind, L. (1995). The world as a hologram. *Journal of Mathematical Physics*, 36(11).
12. 't Hooft, G. (1993). Dimensional reduction in quantum gravity. *arXiv:gr-qc/9310026*.
13. Wheeler, J. A. (1989). Information, physics, quantum: The search for links. *Proceedings of the 3rd International Symposium on Foundations of Quantum Mechanics*.
14. Friston, K. (2010). The free-energy principle: a unified brain theory. *Nature Reviews Neuroscience*, 11(2).
15. Hawking, S. W. (1975). Particle creation by black holes. *Communications in Mathematical Physics*, 43(3).
16. Mazur, M. (2026f). *Cynober DB: HSL Reference Implementation*. GitHub: Maciej-EriAmo/DBase. PyPI: https://pypi.org/project/cynober-db/

---

*— Maciej Mazur, Warsaw, 2026*
