document.addEventListener('DOMContentLoaded', () => {
    // Ensure dark mode only
    document.body.classList.remove('light-mode');
    try {
        localStorage.removeItem('theme');
    } catch(e) {}

    // --- Navigation Logic ---
    const navBtns = document.querySelectorAll('.nav-btn');
    const sections = document.querySelectorAll('.content-section');

    function showSection(sectionId) {
        navBtns.forEach(b => {
            const isMatch = b.dataset.target === sectionId;
            b.classList.toggle('tab-active', isMatch);
            b.classList.toggle('text-slate-400', !isMatch);
        });
        sections.forEach(sec => sec.classList.toggle('hidden-section', sec.id !== sectionId));
    }

    navBtns.forEach(btn => {
        btn.addEventListener('click', () => showSection(btn.dataset.target));
    });

    // Deep links (e.g. #sec-comparison-matrix) open the tab that contains the target.
    function revealHashTarget() {
        const id = decodeURIComponent(window.location.hash.slice(1));
        const target = id ? document.getElementById(id) : null;
        const section = target ? target.closest('.content-section') : null;
        if(!section) return;
        if(section.classList.contains('hidden-section')) showSection(section.id);
        requestAnimationFrame(() => target.scrollIntoView());
    }
    window.addEventListener('hashchange', revealHashTarget);

    // --- Chart.js Initialization ---
    if (typeof Chart !== 'undefined') {
        Chart.defaults.color = '#94a3b8';
        Chart.defaults.font.family = "'JetBrains Mono', monospace";
        
        const chartCanvas = document.getElementById('encodeChart');
        if (chartCanvas) {
            const ctx = chartCanvas.getContext('2d');
            encodeChart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: ['Serial', 'Parallel'],
                    datasets: [
                        { label: 'Chunk 1', data: [240, 60], backgroundColor: '#06b6d4', barThickness: 40 },
                        { label: 'Chunk 2', data: [0, 60], backgroundColor: '#8b5cf6', barThickness: 40 },
                        { label: 'Stitch Overhead', data: [0, 5], backgroundColor: '#d946ef', barThickness: 40 }
                    ]
                },
                options: {
                    indexAxis: 'y',
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { position: 'bottom' },
                        tooltip: { callbacks: { label: (c) => c.dataset.label } }
                    },
                    scales: {
                        x: { stacked: true, grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { display: false } },
                        y: { stacked: true, grid: { display: false } }
                    }
                }
            });
        }
    }

    // --- BPP Calculator Logic ---
    const MIN_BPP = 0.04;
    const MB_TO_BITS = 8 * 1024 * 1024;

    function calculateBPP() {
        const targetMB = parseFloat(document.getElementById('calc-mb').value) || 100;
        const duration = parseFloat(document.getElementById('calc-dur').value) || 120;
        const srcW = parseInt(document.getElementById('calc-w').value) || 1920;
        const srcH = parseInt(document.getElementById('calc-h').value) || 1080;
        const srcFps = parseFloat(document.getElementById('calc-fps').value) || 60;

        const targetBits = targetMB * MB_TO_BITS;
        const aspectRatio = srcW / srcH;

        // Filter Options
        let heightOptions = [2160, 1440, 1080, 720];
        let fpsOptions = [120.0, 90.0, 60.0];

        let validHeights = heightOptions.filter(h => h <= srcH);
        if (!validHeights.includes(srcH)) validHeights.unshift(srcH);

        let validFps = fpsOptions.filter(f => f <= srcFps);
        if (!validFps.includes(srcFps)) validFps.unshift(srcFps);

        let candidates = [];
        validHeights.forEach(h => {
            const w = Math.round(h * aspectRatio);
            validFps.forEach(f => {
                const pixelsPerSec = w * h * f;
                const bpp = targetBits / (duration * pixelsPerSec);
                candidates.push({ h, w, f, bpp, pps: pixelsPerSec, fpsPriority: f >= 60 });
            });
        });

        // Sort logic: Top candidates > MIN_BPP, then FPS >= 60, then throughput
        let safeOnes = candidates.filter(c => c.bpp >= MIN_BPP);
        safeOnes.sort((a, b) => {
            if (a.fpsPriority !== b.fpsPriority) return a.fpsPriority ? -1 : 1;
            return b.pps - a.pps;
        });

        const best = safeOnes.length > 0 ? safeOnes[0] : candidates[candidates.length - 1]; // Fallback to smallest

        // Render Results
        const tbody = document.getElementById('calc-results');
        if(tbody) {
            tbody.innerHTML = '';
            candidates.forEach(c => {
                const isBest = c === best;
                const isSafe = c.bpp >= MIN_BPP;
                
                const tr = document.createElement('tr');
                if (isBest) tr.className = 'bg-cyan-900/30 border-l-2 border-cyan-400';
                tr.innerHTML = `
                    <td class="px-4 py-3 ${isBest ? 'text-cyan-400 font-bold' : ''}">${c.w}x${c.h}</td>
                    <td class="px-4 py-3">${c.f}</td>
                    <td class="px-4 py-3 ${isSafe ? 'text-emerald-400' : 'text-rose-400'}">${c.bpp.toFixed(4)}</td>
                    <td class="px-4 py-3 uppercase text-[10px]">${isSafe ? '<span class="text-emerald-400">Pass</span>' : '<span class="text-rose-400">Fail</span>'}</td>
                `;
                tbody.appendChild(tr);
            });
        }

        const conc = document.getElementById('calc-conclusion');
        if(conc) {
            conc.classList.remove('hidden', 'border-emerald-500', 'bg-emerald-900/20', 'border-amber-500', 'bg-amber-900/20');
            if (best.bpp >= MIN_BPP) {
                conc.classList.add('border-emerald-500', 'bg-emerald-900/20');
                conc.innerHTML = `<strong>Quality Pass:</strong> Script will use <strong>${best.w}x${best.h} @ ${best.f}fps</strong>. Clarity threshold maintained.`;
            } else {
                conc.classList.add('border-amber-500', 'bg-amber-900/20');
                conc.innerHTML = `<strong>Downscale Required:</strong> Target size is too restrictive. Scaling to <strong>${best.w}x${best.h} @ ${best.f}fps</strong> to avoid pixelation.`;
            }
        }
    }

    const debounce = (f, w) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => f(...a), w); }; };
    const debouncedCalc = debounce(calculateBPP, 500);
    ['calc-mb', 'calc-dur', 'calc-w', 'calc-h', 'calc-fps'].forEach(id => {
        const el = document.getElementById(id);
        if(el) el.addEventListener('input', debouncedCalc);
    });
    calculateBPP();

    // --- Hardware Matrix Logic ---
    const osData = {
        win: {
            title: "Windows Execution Priority", icon: "fa-windows", color: "text-blue-400",
            chain: [
                { name: "Nvidia NVENC", desc: "H.265/H.264 via dedicated hardware. Supports parallel split single-pass.", icon: "fa-microchip" },
                { name: "AMD AMF", desc: "H.265/H.264 via AMD's Media Framework.", icon: "fa-microchip" },
                { name: "Intel Quick Sync (QSV)", desc: "H.265/H.264 via Intel's integrated GPU.", icon: "fa-microchip" },
                { name: "CPU Fallback (libx265/libx264)", desc: "Software encoding. Most compatible, but slowest.", icon: "fa-server" }
            ],
            note: "The script probes for encoders for the selected codec (H.265/HEVC or H.264). If dedicated GPU encoding fails or is unavailable, it defaults to CPU."
        },
        lin: {
            title: "Linux Execution Priority", icon: "fa-linux", color: "text-yellow-400",
            chain: [
                { name: "Nvidia NVENC", desc: "H.265/H.264 via dedicated hardware. Supports parallel split single-pass.", icon: "fa-microchip" },
                { name: "VA-API", desc: "Unified API for AMD and Intel hardware acceleration.", icon: "fa-layer-group" },
                { name: "CPU Fallback (libx265/libx264)", desc: "Software encoding. Most compatible, but slowest.", icon: "fa-server" }
            ],
            note: "VA-API is a versatile API that covers both AMD and Intel hardware on Linux. NVENC is preferred if an Nvidia GPU is present."
        },
        mac: {
            title: "macOS Execution Priority", icon: "fa-apple", color: "text-slate-200",
            chain: [
                { name: "Apple VideoToolbox", desc: "Native API for Apple Silicon, AMD, and Intel hardware.", icon: "fa-apple" },
                { name: "CPU Fallback (libx265/libx264)", desc: "Software encoding. Most compatible, but slowest.", icon: "fa-server" }
            ],
            note: "VideoToolbox is the native macOS framework that abstracts hardware (Apple Silicon, Intel, AMD). Nvidia encoding is not supported on modern macOS."
        }
    };

    function renderChain(k) {
        const d = osData[k];
        const osTitle = document.getElementById('os-title');
        const osNote = document.getElementById('os-note');
        const priorityChain = document.getElementById('priority-chain');

        if(osTitle && osNote && priorityChain) {
            osTitle.innerHTML = `<i class="fa-brands ${d.icon} ${d.color} mr-3"></i>${d.title}`;
            osNote.textContent = d.note;
            priorityChain.innerHTML = '';
            d.chain.forEach((e, i) => {
                const el = document.createElement('div');
                el.className = `flex items-center p-4 bg-slate-800/50 rounded-lg border border-slate-700 hover:border-slate-500 transition-all ${i===0?'ring-1 ring-cyan-500/30':''}`;
                el.innerHTML = `<div class="w-8 h-8 rounded-full ${i===0?'bg-cyan-500 text-slate-900':'bg-slate-700 text-white'} flex items-center justify-center font-bold text-sm mr-4">${i+1}</div><div class="flex-grow"><div class="font-bold text-white font-mono text-sm">${e.name}</div><div class="text-xs text-slate-400">${e.desc}</div></div><i class="fa-solid ${e.icon} text-slate-600 text-xl ml-4"></i>`;
                priorityChain.appendChild(el);
            });
        }
    }

    document.querySelectorAll('.os-btn').forEach(b => b.addEventListener('click', e => {
        document.querySelectorAll('.os-btn').forEach(x => { x.classList.remove('os-btn-active'); x.classList.add('bg-slate-800', 'border-slate-600'); });
        const btn = e.target.closest('button'); 
        if(btn) {
            btn.classList.remove('bg-slate-800', 'border-slate-600'); 
            btn.classList.add('os-btn-active');
            renderChain(btn.id.replace('btn-', ''));
        }
    }));

    renderChain('win');

    // --- Dataflow Visualizer Logic ---
    const dataflowProfiles = {
        unscaled: {
            title: "Hardware Native Unscaled (1080p60)",
            badge: "Direct Hardware Path",
            badgeClass: "bg-cyan-900/40 text-cyan-400 border-cyan-500/40",
            note: "Zero-copy VRAM execution with zero software scaling copies.",
            summary: "Frames are demuxed from storage and streamed as compressed NAL packets across PCIe into GPU VRAM. NVDEC decodes into CUDA surfaces, the frame scaling filter is completely bypassed (zero software pixel copies), and NVENC encodes directly on-chip. Only compressed bitstream traverses PCIe.",
            pcie: "~545 MB Total",
            pcieSub: "99.1% Bus Reduction",
            pixfmt: "pix_fmt: cuda",
            pixfmtSub: "VRAM Hardware Surface",
            scale: "Bypassed",
            scaleSub: "Zero Pixel Copies",
            cpu: "< 5% Load",
            cpuSub: "Light Demux / Telemetry",
            scaleLabel: "CPU Lanczos Scale",
            scaleStatus: "Bypassed (Native)",
            h2dVol: "~457 MB (Stream)",
            d2hVol: "~88.5 MB (Muxed)",
            busStatus: "Minimal (~2 MB/s)",
            busStatusClass: "text-emerald-400",
            nodes: { storage: true, host: true, pcie: true, gpu: true }
        },
        downscaled: {
            title: "Hardware Downscaled (Legacy 720p60 Hybrid)",
            badge: "PCIe Roundtrip Path",
            badgeClass: "bg-amber-900/40 text-amber-400 border-amber-500/40",
            note: "Legacy v1.1.6 hybrid pipeline: decoded on NVDEC, downloaded to Host RAM for CPU Lanczos scaling, and uploaded back to NVENC.",
            summary: "Legacy v1.1.6 hybrid pipeline (CPU swscale roundtrip): NVDEC decompresses 1080p frames in VRAM. Because standard scale is a CPU software filter (libswscale), FFmpeg automatically downloads decoded frames across PCIe to Host System RAM (~44 GB D2H). The CPU runs Lanczos scaling on the host, and uploads 720p frames across PCIe back to NVENC (~19 GB H2D) for encoding.",
            pcie: "~64.4 GB Total",
            pcieSub: "44.2 GB D2H + 19.6 GB H2D",
            pixfmt: "pix_fmt: nv12",
            pixfmtSub: "Software Host Buffer",
            scale: "CPU Software",
            scaleSub: "Lanczos Filter (libswscale)",
            cpu: "~30% - 40%",
            cpuSub: "CPU Scaling Workload",
            scaleLabel: "CPU Lanczos Scale",
            scaleStatus: "Active (720p)",
            h2dVol: "~19.65 GB (720p)",
            d2hVol: "~44.22 GB (1080p)",
            busStatus: "Heavy (~1.6 GB/s)",
            busStatusClass: "text-amber-400",
            nodes: { storage: true, host: true, pcie: true, gpu: true }
        },
        cpu: {
            title: "CPU Software Fallback (libx265 / libx264)",
            badge: "Pure Host RAM Path",
            badgeClass: "bg-violet-900/40 text-violet-400 border-violet-500/40",
            note: "Fully host-bound execution. No GPU silicon or PCIe bus utilization.",
            summary: "Executed when no compatible hardware GPU encoder is available. Video decoding, Lanczos scaling, and HEVC/H.264 compression operate entirely within host CPU threads and System RAM. No frames or bitstreams cross the PCIe bus into GPU memory.",
            pcie: "0 MB",
            pcieSub: "Zero PCIe Bus Activity",
            pixfmt: "yuv420p / nv12",
            pixfmtSub: "Host System Memory",
            scale: "CPU Software",
            scaleSub: "Lanczos on CPU Cores",
            cpu: "90% - 100%",
            cpuSub: "Multi-Core CPU Bound",
            scaleLabel: "CPU Lanczos Scale",
            scaleStatus: "Active (CPU Cores)",
            h2dVol: "0 MB (Idle)",
            d2hVol: "0 MB (Idle)",
            busStatus: "Inactive (0 MB/s)",
            busStatusClass: "text-slate-500",
            nodes: { storage: true, host: true, pcie: false, gpu: false }
        },
        zerocopy: {
            title: "Zero-Copy GPU Active (all-GPU scaling, CUDA example)",
            badge: "Current Implementation",
            badgeClass: "bg-emerald-900/40 text-emerald-400 border-emerald-500/40",
            note: "Active current pipeline: decoded, scaled, and encoded entirely inside GPU VRAM without host RAM roundtrips.",
            summary: "The active current hardware pipeline, shown here for NVIDIA with -hwaccel cuda -hwaccel_output_format cuda and scale_cuda=-2:H:interp_algo=lanczos. Other backends follow the same shape with their native scaler (scale_vaapi, vpp_amf, vpp_qsv, scale_vt) and fall back automatically to an upload tier or the legacy path when an input cannot be decoded in hardware. Frames remain in GPU memory (pix_fmt: cuda) throughout the entire lifetime. Decoded by NVDEC, resized by CUDA cores directly in VRAM, and encoded by NVENC. Completely eliminates the 64 GB PCIe roundtrip.",
            pcie: "~545 MB Total",
            pcieSub: "True Zero-Copy Pipeline",
            pixfmt: "pix_fmt: cuda",
            pixfmtSub: "On-Chip VRAM Resident",
            scale: "GPU CUDA Cores",
            scaleSub: "Hardware scale_cuda",
            cpu: "< 3% Load",
            cpuSub: "Near-Zero CPU Footprint",
            scaleLabel: "CUDA Core Resizer",
            scaleStatus: "Active (in VRAM)",
            h2dVol: "~457 MB (Stream)",
            d2hVol: "~88.5 MB (Muxed)",
            busStatus: "Minimal (~2 MB/s)",
            busStatusClass: "text-emerald-400",
            nodes: { storage: true, host: true, pcie: true, gpu: true }
        }
    };

    function renderDataflowMode(modeKey) {
        const p = dataflowProfiles[modeKey] || dataflowProfiles.unscaled;

        const titleEl = document.getElementById('df-mode-title');
        const badgeEl = document.getElementById('df-mode-badge');
        const noteEl = document.getElementById('df-mode-note');
        const summaryEl = document.getElementById('df-mode-summary');

        if(titleEl) titleEl.textContent = p.title;
        if(badgeEl) {
            badgeEl.className = `df-chip ${p.badgeClass}`;
            badgeEl.textContent = p.badge;
        }
        if(noteEl) noteEl.textContent = p.note;
        if(summaryEl) summaryEl.textContent = p.summary;

        // Update Spec Cards
        const mPcie = document.getElementById('metric-pcie');
        const mPcieSub = document.getElementById('metric-pcie-sub');
        const mPixfmt = document.getElementById('metric-pixfmt');
        const mPixfmtSub = document.getElementById('metric-pixfmt-sub');
        const mScale = document.getElementById('metric-scale');
        const mScaleSub = document.getElementById('metric-scale-sub');
        const mCpu = document.getElementById('metric-cpu');
        const mCpuSub = document.getElementById('metric-cpu-sub');

        if(mPcie) mPcie.textContent = p.pcie;
        if(mPcieSub) mPcieSub.textContent = p.pcieSub;
        if(mPixfmt) mPixfmt.textContent = p.pixfmt;
        if(mPixfmtSub) mPixfmtSub.textContent = p.pixfmtSub;
        if(mScale) mScale.textContent = p.scale;
        if(mScaleSub) mScaleSub.textContent = p.scaleSub;
        if(mCpu) mCpu.textContent = p.cpu;
        if(mCpuSub) mCpuSub.textContent = p.cpuSub;

        // Update Domain Nodes
        const domStorage = document.getElementById('domain-storage');
        const domHost = document.getElementById('domain-host');
        const domPcie = document.getElementById('domain-pcie');
        const domGpu = document.getElementById('domain-gpu');

        const setNodeState = (el, active) => {
            if(!el) return;
            el.classList.remove('flow-node-active', 'flow-node-dim');
            el.classList.add(active ? 'flow-node-active' : 'flow-node-dim');
        };

        setNodeState(domStorage, p.nodes.storage);
        setNodeState(domHost, p.nodes.host);
        setNodeState(domPcie, p.nodes.pcie);
        setNodeState(domGpu, p.nodes.gpu);

        // Update Node Sub-elements
        const scaleLbl = document.getElementById('df-scale-label');
        const scaleStat = document.getElementById('df-scale-status');
        const h2dVol = document.getElementById('df-h2d-vol');
        const d2hVol = document.getElementById('df-d2h-vol');
        const busStat = document.getElementById('df-bus-status');

        if(scaleLbl) scaleLbl.textContent = p.scaleLabel;
        if(scaleStat) scaleStat.textContent = p.scaleStatus;
        if(h2dVol) h2dVol.textContent = p.h2dVol;
        if(d2hVol) d2hVol.textContent = p.d2hVol;
        if(busStat) {
            busStat.className = `df-chip ${p.busStatusClass}`;
            busStat.textContent = p.busStatus;
        }
    }

    // --- PHYSICAL SCHEMATIC VIEW CONTROLLER ---
    const schematicBtns = document.querySelectorAll('#schematic-view-controls button');
    const cardPath1 = document.getElementById('schematic-card-path1');
    const cardPath2 = document.getElementById('schematic-card-path2');
    const cardPath3 = document.getElementById('schematic-card-path3');
    const schematicContainer = document.getElementById('schematic-cards-container');

    const schematicViews = {
        path1: [cardPath1],
        path2: [cardPath2],
        path3: [cardPath3],
        compare: [cardPath2, cardPath3],
        all: [cardPath1, cardPath2, cardPath3],
    };

    function setSchematicView(viewKey) {
        const visible = schematicViews[viewKey] || schematicViews.path3;
        schematicBtns.forEach(btn => {
            const isMatch = btn.dataset.schematic === viewKey;
            btn.classList.toggle('schematic-btn-active', isMatch);
            btn.setAttribute('aria-pressed', String(isMatch));
        });
        [cardPath1, cardPath2, cardPath3].forEach(card => {
            if(card) card.classList.toggle('hidden', !visible.includes(card));
        });
        if(schematicContainer) {
            schematicContainer.classList.toggle('schematic-compare', viewKey === 'compare');
        }
    }

    schematicBtns.forEach(btn => {
        btn.addEventListener('click', () => setSchematicView(btn.dataset.schematic));
    });

    const modeBtns = document.querySelectorAll('.df-mode-btn');
    modeBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            modeBtns.forEach(x => {
                const isActive = x === btn;
                x.classList.toggle('mode-btn-active', isActive);
                x.setAttribute('aria-pressed', String(isActive));
            });
            renderDataflowMode(btn.dataset.mode);
        });
    });

    renderDataflowMode('unscaled');

    // --- Dataflow sub-navigation: highlight the block currently in view ---
    const subnavList = document.querySelector('.df-subnav-list');
    const subnavLinks = Array.from(document.querySelectorAll('.df-subnav-link'));
    const subnavBlocks = subnavLinks
        .map(link => document.getElementById(link.hash.slice(1)))
        .filter(Boolean);

    function setCurrentBlock(id) {
        subnavLinks.forEach(link => {
            const isCurrent = link.hash === `#${id}`;
            if(isCurrent) {
                link.setAttribute('aria-current', 'location');
                if(subnavList && subnavList.scrollWidth > subnavList.clientWidth) {
                    subnavList.scrollTo({ left: Math.max(0, link.offsetLeft - 16) });
                }
            } else {
                link.removeAttribute('aria-current');
            }
        });
    }

    // The current block is the last one whose top has passed 35% of the viewport.
    function updateCurrentBlock() {
        const dataflow = document.getElementById('sec-dataflow');
        if(!subnavBlocks.length || !dataflow || dataflow.classList.contains('hidden-section')) return;
        const marker = window.innerHeight * 0.35;
        const atBottom = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2;
        const passed = subnavBlocks.filter(block => block.getBoundingClientRect().top <= marker);
        const current = atBottom ? subnavBlocks[subnavBlocks.length - 1] : (passed[passed.length - 1] || subnavBlocks[0]);
        setCurrentBlock(current.id);
    }

    let subnavFrame = 0;
    window.addEventListener('scroll', () => {
        cancelAnimationFrame(subnavFrame);
        subnavFrame = requestAnimationFrame(updateCurrentBlock);
    }, { passive: true });
    navBtns.forEach(btn => btn.addEventListener('click', () => requestAnimationFrame(updateCurrentBlock)));
    updateCurrentBlock();

    // Open the linked tab right away. Tailwind's CDN styles the page asynchronously, so
    // re-align once on full load unless the reader has already started scrolling.
    revealHashTarget();
    if(document.readyState !== 'complete') {
        let readerMoved = false;
        const markMoved = () => { readerMoved = true; };
        ['wheel', 'touchstart', 'keydown'].forEach(evt => window.addEventListener(evt, markMoved, { once: true, passive: true }));
        window.addEventListener('load', () => {
            if(!readerMoved) revealHashTarget();
        }, { once: true });
    }
});
