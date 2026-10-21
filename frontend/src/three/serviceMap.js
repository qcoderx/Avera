import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import gsap from 'gsap';

const STATUS_COLORS = {
  healthy: '#1ea896',
  watch: '#e99a3e',
  critical: '#ff715b',
  suspect: '#b01352',
  cleared: '#b9aea7',
  proposed: '#e0306f',
  patching: '#e99a3e',
};

const THEMES = {
  light: { board: '#fffdfb', plinth: '#e3d5cc', lane: '#f6efe9', node: '#fffdfb', nodeBase: '#eadfd8', grid: '#dccfc6', edge: '#d3c2b7' },
  dark: { board: '#2a211e', plinth: '#120d0c', lane: '#241c19', node: '#3a2e2a', nodeBase: '#1f1816', grid: '#4a3b35', edge: '#5c4a42' },
};

const COLORS = {
  teal: new THREE.Color('#1ea896'),
  coral: new THREE.Color('#ff715b'),
  violet: new THREE.Color('#8178d8'),
  wine: new THREE.Color('#b01352'),
};

const LANE_SHAPES = { channels: 'tile', services: 'tower', routes: 'hex', partners: 'vault' };
const SHAPE_HEIGHT = { tile: 0.42, tower: 0.78, hex: 0.62, vault: 0.86 };
const PARTICLE_COUNT = 120;
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

export function webglAvailable() {
  try {
    const canvas = document.createElement('canvas');
    return Boolean(window.WebGLRenderingContext && (canvas.getContext('webgl2') || canvas.getContext('webgl')));
  } catch {
    return false;
  }
}

function toWorld(node) {
  return new THREE.Vector3((node.x - 48.5) * 0.25, 0, (node.y - 53) * 0.2);
}

function canvasTexture(size, draw) {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  draw(canvas.getContext('2d'), size);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

function radialTexture(stops) {
  return canvasTexture(128, (ctx, size) => {
    const gradient = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    stops.forEach(([at, alpha]) => gradient.addColorStop(at, `rgba(255,255,255,${alpha})`));
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, size, size);
  });
}

function shapeGeometry(shape, scale = 1) {
  const h = SHAPE_HEIGHT[shape];
  if (shape === 'hex') return new THREE.CylinderGeometry(0.98 * scale, 1.04 * scale, h, 6, 1);
  if (shape === 'vault') return new THREE.CylinderGeometry(0.86 * scale, 0.92 * scale, h, 48, 1);
  if (shape === 'tower') return new RoundedBoxGeometry(2.2 * scale, h, 1.45 * scale, 4, 0.2);
  return new RoundedBoxGeometry(1.9 * scale, h, 1.25 * scale, 4, 0.18);
}

function capGeometry(shape) {
  if (shape === 'hex') return new THREE.CylinderGeometry(0.78, 0.82, 0.1, 6, 1);
  if (shape === 'vault') return new THREE.CylinderGeometry(0.68, 0.7, 0.1, 48, 1);
  if (shape === 'tower') return new RoundedBoxGeometry(1.86, 0.1, 1.1, 2, 0.05);
  return new RoundedBoxGeometry(1.58, 0.1, 0.92, 2, 0.05);
}

function dashedRing(radius, color) {
  const group = new THREE.Group();
  const material = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0, side: THREE.DoubleSide, depthWrite: false });
  const dashes = 18;
  for (let i = 0; i < dashes; i += 1) {
    const arc = new THREE.Mesh(new THREE.RingGeometry(radius, radius + 0.09, 6, 1, (i / dashes) * Math.PI * 2, (Math.PI * 2 / dashes) * 0.55), material);
    arc.rotation.x = -Math.PI / 2;
    group.add(arc);
  }
  group.userData.material = material;
  return group;
}

export class ServiceMap {
  constructor(fixture, { onSelect } = {}) {
    this.fixture = fixture;
    this.onSelect = onSelect;
    this.theme = 'light';
    this.step = -1;
    this.mode = 'full';
    this.selected = null;
    this.hovered = null;
    this.running = false;
    this.lastInteraction = -Infinity;
    this.swayAmount = 0;
    this.swayClock = 0;
    this.focusTween = null;
    this.frameTimes = [];

    this.host = document.createElement('div');
    this.host.className = 'map3d-host';

    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.NeutralToneMapping;
    this.renderer.toneMappingExposure = 1;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.domElement.className = 'map3d-canvas';
    this.host.appendChild(this.renderer.domElement);

    this.labels = new CSS2DRenderer();
    this.labels.domElement.className = 'map3d-labels';
    this.host.appendChild(this.labels.domElement);

    this.scene = new THREE.Scene();
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    this.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    this.scene.environmentIntensity = 0.55;

    this.camera = new THREE.PerspectiveCamera(34, 1.6, 0.1, 200);
    this.camera.position.set(0, 15.5, 17.5);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.07;
    this.controls.enablePan = false;
    this.controls.minPolarAngle = 0.32;
    this.controls.maxPolarAngle = 1.16;
    this.controls.minDistance = 9;
    this.controls.maxDistance = 80;
    this.controls.rotateSpeed = 0.6;
    this.controls.target.set(0.2, 0, 0.4);
    this.controls.addEventListener('start', () => { this.lastInteraction = Infinity; });
    this.controls.addEventListener('end', () => { this.lastInteraction = performance.now(); });

    this.clock = new THREE.Clock();
    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2(9, 9);

    this.buildLights();
    this.buildBoard();
    this.buildNodes();
    this.buildEdges();
    this.buildParticles();
    this.buildEffects();
    this.buildMascots();
    this.bindPointer();

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(this.host);
    document.addEventListener('visibilitychange', () => this.updateLoop());
  }

  buildLights() {
    this.scene.add(new THREE.HemisphereLight('#fffaf5', '#e9ddd5', 1.1));
    const key = new THREE.DirectionalLight('#fff4ea', 2.1);
    key.position.set(-7, 16, 9);
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    Object.assign(key.shadow.camera, { left: -15, right: 15, top: 11, bottom: -11, near: 1, far: 50 });
    key.shadow.bias = -0.0004;
    key.shadow.normalBias = 0.03;
    this.scene.add(key);
    const rim = new THREE.DirectionalLight('#ffd8cf', 0.65);
    rim.position.set(10, 6, -10);
    this.scene.add(rim);
  }

  buildBoard() {
    const t = THEMES[this.theme];
    this.board = new THREE.Group();
    this.scene.add(this.board);

    this.boardMat = new THREE.MeshStandardMaterial({ color: t.board, roughness: 0.92 });
    this.plinthMat = new THREE.MeshStandardMaterial({ color: t.plinth, roughness: 0.95 });
    const top = new THREE.Mesh(new RoundedBoxGeometry(24, 0.5, 15, 6, 0.42), this.boardMat);
    top.position.y = -0.25;
    top.receiveShadow = true;
    const plinth = new THREE.Mesh(new RoundedBoxGeometry(24, 0.5, 15, 6, 0.42), this.plinthMat);
    plinth.position.y = -0.55;
    plinth.receiveShadow = true;
    this.board.add(plinth, top);

    this.gridTexture = canvasTexture(256, (ctx, size) => {
      ctx.clearRect(0, 0, size, size);
      ctx.fillStyle = '#ffffff';
      for (let x = 16; x < size; x += 32) for (let y = 16; y < size; y += 32) {
        ctx.beginPath(); ctx.arc(x, y, 1.6, 0, Math.PI * 2); ctx.fill();
      }
    });
    this.gridTexture.wrapS = this.gridTexture.wrapT = THREE.RepeatWrapping;
    this.gridTexture.repeat.set(12, 7.5);
    this.gridMat = new THREE.MeshBasicMaterial({ map: this.gridTexture, color: t.grid, transparent: true, depthWrite: false });
    const grid = new THREE.Mesh(new THREE.PlaneGeometry(23, 14), this.gridMat);
    grid.rotation.x = -Math.PI / 2;
    grid.position.y = 0.006;
    this.board.add(grid);

    this.laneMat = new THREE.MeshStandardMaterial({ color: t.lane, roughness: 1, transparent: true, opacity: 0.9 });
    const byId = Object.fromEntries(this.fixture.nodes.map((node) => [node.id, node]));
    this.fixture.lanes.forEach((lane, index) => {
      const xs = lane.nodes.map((id) => toWorld(byId[id]).x);
      const cx = xs.reduce((a, b) => a + b, 0) / xs.length;
      if (index % 2 === 0) {
        const band = new THREE.Mesh(new RoundedBoxGeometry(3.6, 0.02, 13.6, 2, 0.01), this.laneMat);
        band.position.set(cx, 0.012, 0);
        band.receiveShadow = true;
        this.board.add(band);
      }
      const el = document.createElement('div');
      el.className = 'map3d-lane';
      el.textContent = lane.label;
      const label = new CSS2DObject(el);
      label.position.set(cx, -0.2, 7.55);
      this.board.add(label);
    });
  }

  buildNodes() {
    const t = THEMES[this.theme];
    this.nodes = new Map();
    this.nodeMat = new THREE.MeshStandardMaterial({ color: t.node, roughness: 0.55, metalness: 0 });
    this.nodeBaseMat = new THREE.MeshStandardMaterial({ color: t.nodeBase, roughness: 0.8 });
    this.ringTexture = radialTexture([[0, 0], [0.62, 0], [0.8, 0.9], [0.9, 0.35], [1, 0]]);
    this.beamTexture = canvasTexture(64, (ctx, size) => {
      const gradient = ctx.createLinearGradient(0, 0, 0, size);
      gradient.addColorStop(0, 'rgba(255,255,255,0)');
      gradient.addColorStop(1, 'rgba(255,255,255,1)');
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, size, size);
    });

    this.fixture.lanes.forEach((lane) => lane.nodes.forEach((id) => {
      const data = this.fixture.nodes.find((node) => node.id === id);
      const shape = LANE_SHAPES[lane.id];
      const height = SHAPE_HEIGHT[shape];
      const group = new THREE.Group();
      const home = toWorld(data);
      group.position.copy(home);

      const lift = new THREE.Group();
      group.add(lift);

      const base = new THREE.Mesh(shapeGeometry(shape, 1.06), this.nodeBaseMat);
      base.scale.y = 0.35;
      base.position.y = height * 0.175;
      base.castShadow = true;
      base.receiveShadow = true;
      const body = new THREE.Mesh(shapeGeometry(shape), this.nodeMat);
      body.position.y = height / 2 + 0.06;
      body.castShadow = true;
      body.receiveShadow = true;
      const capMat = new THREE.MeshStandardMaterial({ color: STATUS_COLORS.healthy, emissive: STATUS_COLORS.healthy, emissiveIntensity: 0.18, roughness: 0.35 });
      const cap = new THREE.Mesh(capGeometry(shape), capMat);
      cap.position.y = height + 0.1;
      cap.castShadow = true;
      lift.add(base, body, cap);

      const beamMat = new THREE.MeshBasicMaterial({ color: STATUS_COLORS.critical, alphaMap: this.beamTexture, transparent: true, opacity: 0, side: THREE.DoubleSide, depthWrite: false });
      const beam = new THREE.Mesh(new THREE.CylinderGeometry(0.9, 1.05, 3.2, 40, 1, true), beamMat);
      beam.position.y = height + 1.6;
      lift.add(beam);

      const pulses = [0, 1].map(() => {
        const mat = new THREE.MeshBasicMaterial({ color: STATUS_COLORS.critical, map: this.ringTexture, transparent: true, opacity: 0, depthWrite: false });
        const ring = new THREE.Mesh(new THREE.PlaneGeometry(3.4, 3.4), mat);
        ring.rotation.x = -Math.PI / 2;
        ring.position.y = 0.03;
        group.add(ring);
        return ring;
      });

      const halo = dashedRing(1.55, STATUS_COLORS.suspect);
      halo.position.y = 0.04;
      group.add(halo);

      const el = document.createElement('div');
      el.className = 'map3d-label';
      el.innerHTML = `<div class="map3d-card"><span class="map3d-dot"></span><strong>${data.name}</strong><small></small><em hidden></em></div>`;
      const label = new CSS2DObject(el);
      label.center.set(0.5, 1);
      label.position.y = height + 0.75;
      lift.add(label);

      [base, body, cap].forEach((mesh) => { mesh.userData.nodeId = id; });
      this.scene.add(group);
      this.nodes.set(id, {
        id, data, shape, height, group, lift, cap, capMat, beam, beamMat, pulses, halo, el, label, home,
        status: 'healthy', color: new THREE.Color(STATUS_COLORS.healthy), raise: 0, hover: 0, pulse: 0, introY: 0,
        pickables: [base, body, cap],
      });
    }));
  }

  buildEdges() {
    const t = THEMES[this.theme];
    this.edges = new Map();
    this.edgeMat = new THREE.MeshStandardMaterial({ color: t.edge, roughness: 0.6 });
    this.fixture.edges.forEach(([from, to, kind]) => {
      const a = this.nodes.get(from);
      const b = this.nodes.get(to);
      const start = a.home.clone().setY(a.height + 0.16);
      const end = b.home.clone().setY(b.height + 0.16);
      const dist = start.distanceTo(end);
      const lift = 0.55 + dist * 0.13 + (kind === 'reroute' ? 0.9 : 0);
      const dir = end.clone().sub(start).multiplyScalar(0.28);
      const curve = new THREE.CubicBezierCurve3(
        start,
        start.clone().add(dir).add(new THREE.Vector3(0, lift, 0)),
        end.clone().sub(dir).add(new THREE.Vector3(0, lift, 0)),
        end,
      );
      const geometry = new THREE.TubeGeometry(curve, 72, kind === 'reroute' ? 0.08 : 0.06, 10, false);
      const material = kind === 'reroute'
        ? new THREE.MeshStandardMaterial({ color: STATUS_COLORS.proposed, emissive: STATUS_COLORS.proposed, emissiveIntensity: 0.35, roughness: 0.4, transparent: true, opacity: 1 })
        : this.edgeMat.clone();
      const mesh = new THREE.Mesh(geometry, material);
      mesh.castShadow = true;
      const total = geometry.index.count;
      geometry.setDrawRange(0, kind === 'reroute' ? 0 : total);
      this.scene.add(mesh);
      this.edges.set(`${from}>${to}`, { from, to, kind, curve, length: curve.getLength(), mesh, material, total, draw: kind === 'reroute' ? 0 : 1, alert: 0 });
    });
  }

  buildParticles() {
    const geometry = new THREE.SphereGeometry(0.13, 14, 10);
    const material = new THREE.MeshBasicMaterial({ toneMapped: false });
    this.particles = new THREE.InstancedMesh(geometry, material, PARTICLE_COUNT);
    this.particles.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    this.particles.frustumCulled = false;
    this.particleState = [];
    const color = new THREE.Color();
    for (let i = 0; i < PARTICLE_COUNT; i += 1) {
      this.particles.setColorAt(i, color.copy(COLORS.teal));
      this.particleState.push({ alive: false, wait: Math.random() * 2.6 });
    }
    this.scene.add(this.particles);
    this.tmpMatrix = new THREE.Matrix4();
    this.tmpQuat = new THREE.Quaternion();
    this.tmpScale = new THREE.Vector3();
    this.tmpPos = new THREE.Vector3();
    this.tmpColor = new THREE.Color();
  }

  buildEffects() {
    this.sparks = [0, 1, 2].map(() => {
      const mat = new THREE.MeshBasicMaterial({ color: STATUS_COLORS.critical, map: this.ringTexture, transparent: true, opacity: 0, depthWrite: false });
      const mesh = new THREE.Mesh(new THREE.PlaneGeometry(1.6, 1.6), mat);
      this.scene.add(mesh);
      return { mesh, mat, life: 1 };
    });
    this.sparkIndex = 0;
    this.lastSpark = 0;

    const blastTexture = radialTexture([[0, 0.05], [0.7, 0.22], [0.93, 0.9], [1, 0]]);
    this.blastMat = new THREE.MeshBasicMaterial({ color: STATUS_COLORS.critical, map: blastTexture, transparent: true, opacity: 0, depthWrite: false });
    this.blast = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), this.blastMat);
    this.blast.rotation.x = -Math.PI / 2;
    this.blast.position.copy(this.nodes.get('bankx').home).setY(0.05);
    this.scene.add(this.blast);
    this.blastLevel = 0;

    const { complained, affected } = this.fixture.impact;
    const format = new Intl.NumberFormat('en-NG');
    this.blastCallout = document.createElement('div');
    this.blastCallout.className = 'map3d-callout';
    this.blastCallout.innerHTML = `<div class="map3d-card"><span>Blast radius</span><strong>${format.format(affected)}</strong><small>customers affected · ${format.format(complained)} complained</small></div>`;
    this.host.appendChild(this.blastCallout);

    const release = document.createElement('div');
    release.className = 'map3d-badge';
    release.innerHTML = '<div class="map3d-card">App v4.2 released</div>';
    this.releaseLabel = new CSS2DObject(release);
    this.releaseLabel.center.set(0, 0.5);
    this.releaseLabel.position.copy(this.nodes.get('app').home).add(new THREE.Vector3(1.15, 0.3, 0.5));
    this.scene.add(this.releaseLabel);
  }

  buildMascots() {
    const loader = new THREE.TextureLoader();
    const load = (agent, frame) => {
      const texture = loader.load(`/assets/agents/${agent}/agent-${frame}.png`);
      texture.colorSpace = THREE.SRGBColorSpace;
      return texture;
    };
    const shadowTexture = radialTexture([[0, 0.5], [0.5, 0.22], [1, 0]]);
    const make = (agent, modes) => {
      const frames = Object.fromEntries(Object.entries(modes).map(([mode, list]) => [mode, list.map((frame) => load(agent, frame))]));
      const material = new THREE.SpriteMaterial({ map: Object.values(frames)[0][0], transparent: true, depthWrite: false, opacity: 1 });
      const sprite = new THREE.Sprite(material);
      sprite.center.set(0.5, 0.05);
      sprite.scale.setScalar(0.001);
      const shadow = new THREE.Mesh(new THREE.PlaneGeometry(1.3, 0.7), new THREE.MeshBasicMaterial({ color: '#523f38', map: shadowTexture, transparent: true, opacity: 0, depthWrite: false }));
      shadow.rotation.x = -Math.PI / 2;
      const group = new THREE.Group();
      group.add(sprite, shadow);
      this.scene.add(group);
      return { agent, frames, mode: Object.keys(modes)[0], sprite, material, shadow, group, size: 0, target: new THREE.Vector3(), visible: false };
    };
    this.mascots = {
      observability: make('observability', { listening: ['listen-a', 'listen-b'], working: ['work-a', 'work-b'], result: ['result'] }),
      ghost: make('ghost', { working: ['work-a', 'work-b'], result: ['result'] }),
      blast: make('blast', { working: ['work-a', 'work-b'], result: ['result'] }),
      resolution: make('resolution', { listening: ['listen-a', 'listen-b'], result: ['result'] }),
    };
  }

  bindPointer() {
    const canvas = this.renderer.domElement;
    let down = null;
    canvas.addEventListener('pointermove', (event) => {
      const rect = canvas.getBoundingClientRect();
      this.pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
    });
    canvas.addEventListener('pointerleave', () => this.pointer.set(9, 9));
    canvas.addEventListener('pointerdown', (event) => { down = [event.clientX, event.clientY]; });
    canvas.addEventListener('pointerup', (event) => {
      if (this.mode !== 'full' || !down || Math.hypot(event.clientX - down[0], event.clientY - down[1]) > 6) return;
      down = null;
      const id = this.hovered;
      this.select(id && id !== this.selected ? id : null);
      this.onSelect?.(this.selected);
    });
  }

  mount(container, { mode = 'full', intro = false } = {}) {
    this.mode = mode;
    container.appendChild(this.host);
    this.host.dataset.mode = mode;
    this.controls.enableZoom = mode === 'full';
    this.controls.enabled = !(mode === 'compact' && window.matchMedia('(pointer: coarse)').matches);
    this.renderer.domElement.style.touchAction = this.controls.enabled ? 'none' : 'pan-y';
    this.resize();
    if (intro) this.playIntro();
    this.updateLoop(true);
  }

  sleep() {
    this.running = false;
    this.renderer.setAnimationLoop(null);
  }

  updateLoop(force = false) {
    const shouldRun = this.host.isConnected && !document.hidden;
    if (shouldRun && (!this.running || force)) {
      this.running = true;
      this.clock.getDelta();
      this.renderer.setAnimationLoop(() => this.tick());
    } else if (!shouldRun) this.sleep();
  }

  // Pull the camera back until every board corner sits inside the frame.
  homeView() {
    const compact = this.mode === 'compact';
    const direction = (compact ? new THREE.Vector3(-0.14, 0.52, 0.84) : new THREE.Vector3(-0.2, 0.7, 0.7)).normalize();
    const target = new THREE.Vector3(0, compact ? 0.2 : 0.6, compact ? 0.3 : 0.7);
    const probe = this.camera.clone();
    const corner = new THREE.Vector3();
    const margin = compact ? 0.97 : 0.93;
    let distance = 14;
    for (; distance < 90; distance += 0.4) {
      probe.position.copy(target).addScaledVector(direction, distance);
      probe.lookAt(target);
      probe.updateMatrixWorld();
      const fits = [-12, 12].every((x) => [-7.5, 7.5].every((z) => [0, -0.8].every((y) => {
        corner.set(x, y, z).project(probe);
        return Math.abs(corner.x) <= margin && Math.abs(corner.y) <= margin - 0.04;
      })));
      if (fits) break;
    }
    return { position: probe.position.clone(), target };
  }

  resize() {
    const rect = this.host.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const changed = Math.abs(rect.width - (this.width || 0)) > 1 || Math.abs(rect.height - (this.height || 0)) > 1;
    this.width = rect.width;
    this.height = rect.height;
    this.host.classList.toggle('tight', rect.width < 620);
    this.renderer.setSize(rect.width, rect.height, false);
    this.labels.setSize(rect.width, rect.height);
    this.camera.aspect = rect.width / rect.height;
    this.camera.updateProjectionMatrix();
    if (changed && !this.selected && !this.introPlaying) {
      const home = this.homeView();
      this.camera.position.copy(home.position);
      this.controls.target.copy(home.target);
    }
  }

  playIntro() {
    const home = this.homeView();
    this.introPlaying = true;
    this.clearSelection();
    if (reducedMotion) {
      this.camera.position.copy(home.position);
      this.controls.target.copy(home.target);
      this.introPlaying = false;
      return;
    }
    const from = home.position.clone().applyAxisAngle(new THREE.Vector3(0, 1, 0), -0.55).multiplyScalar(1.35).add(new THREE.Vector3(0, 6, 0));
    this.camera.position.copy(from);
    this.controls.target.copy(home.target);
    gsap.to(this.camera.position, { x: home.position.x, y: home.position.y, z: home.position.z, duration: 2.1, ease: 'expo.out', onComplete: () => { this.introPlaying = false; } });

    gsap.fromTo(this.board.scale, { x: 0.86, y: 0.86, z: 0.86 }, { x: 1, y: 1, z: 1, duration: 1.4, ease: 'expo.out' });
    [...this.nodes.values()].sort((a, b) => a.home.x - b.home.x || a.home.z - b.home.z).forEach((node, index) => {
      node.introY = 5.5;
      node.group.scale.setScalar(0.4);
      gsap.to(node, { introY: 0, duration: 1.15, delay: 0.18 + index * 0.06, ease: 'bounce.out' });
      gsap.to(node.group.scale, { x: 1, y: 1, z: 1, duration: 0.9, delay: 0.18 + index * 0.06, ease: 'back.out(2)' });
    });
    this.edges.forEach((edge, key) => {
      if (edge.kind === 'reroute') return;
      edge.draw = 0;
      gsap.to(edge, { draw: 1, duration: 1.1, delay: 0.75 + (edge.curve.v0.x + 10) * 0.045, ease: 'power2.inOut' });
    });
    this.particleState.forEach((p) => { p.alive = false; p.wait = 1.4 + Math.random() * 2.2; });
  }

  setTheme(theme) {
    if (theme === this.theme) return;
    this.theme = theme;
    const t = THEMES[theme];
    this.boardMat.color.set(t.board);
    this.plinthMat.color.set(t.plinth);
    this.laneMat.color.set(t.lane);
    this.nodeMat.color.set(t.node);
    this.nodeBaseMat.color.set(t.nodeBase);
    this.gridMat.color.set(t.grid);
    this.edges.forEach((edge) => { if (edge.kind !== 'reroute') edge.material.color.set(t.edge); });
    this.edgeMat.color.set(t.edge);
  }

  setStep(step) {
    if (step === this.step) return;
    const state = this.fixture.mapStates[step];
    this.step = step;
    this.state = state;
    const duration = reducedMotion ? 0 : 0.9;

    this.nodes.forEach((node) => {
      const status = state.status[node.id] || 'healthy';
      const metric = state.metrics[node.id] || node.data.metric;
      const tag = state.tags[node.id] || '';
      const changed = status !== node.status;
      node.status = status;
      node.el.className = `map3d-label status-${status}${this.selected === node.id ? ' selected' : ''}`;
      node.el.querySelector('small').textContent = metric;
      const em = node.el.querySelector('em');
      em.textContent = tag;
      em.hidden = !tag;
      const color = new THREE.Color(STATUS_COLORS[status]);
      gsap.to(node.color, { r: color.r, g: color.g, b: color.b, duration, ease: 'power2.out' });
      const raise = status === 'critical' ? 0.5 : status === 'proposed' ? 0.3 : 0;
      gsap.to(node, { raise, duration: duration * 1.2, ease: 'back.out(2.2)' });
      gsap.to(node.halo.userData.material, { opacity: status === 'suspect' || status === 'proposed' ? 0.9 : 0, duration });
      node.halo.userData.material.color.set(status === 'proposed' ? STATUS_COLORS.proposed : STATUS_COLORS.suspect);
      if (changed && status !== 'healthy' && duration) {
        gsap.fromTo(node.lift.scale, { x: 1.18, y: 0.78, z: 1.18 }, { x: 1, y: 1, z: 1, duration: 0.9, ease: 'elastic.out(1, 0.4)' });
      }
    });

    this.edges.forEach((edge) => {
      const alert = state.failing && edge.from === 'gb' && edge.to === 'bankx' && state.status.gb === 'critical' ? 1 : 0;
      gsap.to(edge, { alert, duration });
      if (edge.kind === 'reroute') {
        const draw = state.reroute === 'hidden' ? 0 : 1;
        gsap.to(edge, { draw, duration: duration * 1.6, ease: 'power3.inOut' });
        const color = new THREE.Color(state.reroute === 'active' ? STATUS_COLORS.healthy : STATUS_COLORS.proposed);
        gsap.to(edge.material.color, { r: color.r, g: color.g, b: color.b, duration });
        gsap.to(edge.material.emissive, { r: color.r, g: color.g, b: color.b, duration });
      }
    });
    const gb = this.edges.get('gb>bankx');
    gsap.to(gb.material, { opacity: state.reroute === 'active' ? 0.35 : 1, duration });
    gb.material.transparent = true;

    gsap.to(this, { blastLevel: state.blast ? 1 : 0, duration });
    this.blastCallout.classList.toggle('visible', state.blast);
    this.releaseLabel.element.classList.toggle('visible', state.release);
    this.placeMascots(step);
  }

  placeMascots(step) {
    const at = (id, offset) => this.nodes.get(id).home.clone().add(offset);
    const plan = {
      observability: step <= 2 ? { mode: step === 2 ? 'working' : 'listening', pos: step === 2 ? at('gb', new THREE.Vector3(1.1, 0, -1.9)) : at('api', new THREE.Vector3(-0.2, 0, -1.9)) } : null,
      ghost: step === 3 ? { mode: 'working', ride: true } : null,
      blast: step === 4 || step === 5 ? { mode: step === 4 ? 'working' : 'result', pos: at('bankx', new THREE.Vector3(-0.15, 0, -1.85)) } : null,
      resolution: step >= 5 ? { mode: step === 5 ? 'listening' : 'result', pos: at('gc', new THREE.Vector3(1.15, 0, 1.1)) } : null,
    };
    Object.entries(this.mascots).forEach(([id, mascot]) => {
      const next = plan[id];
      const duration = reducedMotion ? 0 : 0.7;
      if (!next) {
        if (mascot.visible) gsap.to(mascot, { size: 0, duration: duration * 0.6, ease: 'back.in(2)' });
        mascot.visible = false;
        return;
      }
      mascot.mode = next.mode;
      mascot.ride = Boolean(next.ride);
      if (next.pos) {
        if (!mascot.visible) mascot.group.position.copy(next.pos);
        else gsap.to(mascot.group.position, { x: next.pos.x, y: next.pos.y, z: next.pos.z, duration: 1.4, ease: 'power3.inOut' });
      }
      if (!mascot.visible) gsap.to(mascot, { size: 1, duration, delay: 0.25, ease: 'back.out(2.4)' });
      mascot.visible = true;
    });
  }

  clearSelection() {
    this.selected = null;
    this.focusTween?.kill();
    this.nodes.forEach((node) => node.el.classList.remove('selected'));
  }

  select(id) {
    this.clearSelection();
    this.selected = id;
    this.nodes.forEach((node) => node.el.classList.toggle('selected', node.id === id));
    const duration = reducedMotion ? 0.01 : 1.3;
    let position;
    let target;
    if (id) {
      const node = this.nodes.get(id);
      target = node.home.clone().setY(0.6);
      const offset = this.camera.position.clone().sub(this.controls.target).setLength(13.5);
      offset.y = Math.max(offset.y, 7.5);
      position = target.clone().add(offset);
    } else {
      ({ position, target } = this.homeView());
    }
    this.focusTween = gsap.timeline()
      .to(this.camera.position, { x: position.x, y: position.y, z: position.z, duration, ease: 'power3.inOut' }, 0)
      .to(this.controls.target, { x: target.x, y: target.y, z: target.z, duration, ease: 'power3.inOut' }, 0);
    this.lastInteraction = performance.now();
  }

  focusSuspect() {
    const suspect = [...this.nodes.values()].find((node) => node.status === 'critical');
    this.select(suspect ? suspect.id : null);
    this.onSelect?.(this.selected);
  }

  resetView() {
    this.select(null);
    this.onSelect?.(null);
  }

  activeFlows() {
    const state = this.state || this.fixture.mapStates[0];
    return this.fixture.flows.filter((flow) => !flow.probe || state.probes);
  }

  spawn(p) {
    const flows = this.activeFlows();
    const total = flows.reduce((sum, flow) => sum + flow.weight, 0);
    let pick = Math.random() * total;
    const flow = flows.find((item) => (pick -= item.weight) < 0) || flows[0];
    const rerouted = this.state?.reroute === 'active' && flow.rerouted;
    p.flow = flow;
    p.path = rerouted ? flow.rerouted : flow.path;
    p.seg = 0;
    p.t = 0;
    p.speed = (flow.probe ? 3.6 : 2.6) * (0.8 + Math.random() * 0.4) * (reducedMotion ? 0.4 : 1);
    p.alive = true;
    p.dying = 0;
  }

  // Drop to 1x resolution on machines that cannot hold a smooth frame rate.
  adaptQuality(delta) {
    if (this.renderer.getPixelRatio() <= 1) return;
    this.frameTimes.push(delta);
    if (this.frameTimes.length < 90) return;
    const average = this.frameTimes.reduce((a, b) => a + b, 0) / this.frameTimes.length;
    this.frameTimes = [];
    if (average > 1 / 35) {
      this.renderer.setPixelRatio(1);
      this.width = 0;
      this.resize();
    }
  }

  tick() {
    const delta = this.clock.getDelta();
    this.adaptQuality(delta);
    const dt = Math.min(delta, 0.05);
    const time = this.clock.elapsedTime;
    this.swayClock += dt;

    if (this.introPlaying || this.focusTween?.isActive()) {
      this.camera.lookAt(this.controls.target);
    } else {
      const idle = performance.now() - this.lastInteraction > 4500 && !this.selected && !reducedMotion;
      this.swayAmount += ((idle ? 1 : 0) - this.swayAmount) * Math.min(1, dt * 0.8);
      if (this.swayAmount > 0.001) {
        const offset = this.camera.position.clone().sub(this.controls.target);
        offset.applyAxisAngle(new THREE.Vector3(0, 1, 0), Math.cos(this.swayClock * 0.18) * 0.02 * dt * this.swayAmount);
        this.camera.position.copy(this.controls.target).add(offset);
      }
      this.controls.update();
    }

    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hits = this.raycaster.intersectObjects([...this.nodes.values()].flatMap((node) => node.pickables), false);
    const hovered = hits[0]?.object.userData.nodeId || null;
    if (hovered !== this.hovered) {
      this.hovered = hovered;
      this.renderer.domElement.style.cursor = hovered ? 'pointer' : '';
      this.nodes.forEach((node) => node.el.classList.toggle('hover', node.id === hovered));
    }

    this.nodes.forEach((node) => {
      node.hover += ((node.id === this.hovered || node.id === this.selected ? 1 : 0) - node.hover) * Math.min(1, dt * 10);
      const critical = node.status === 'critical';
      const bob = critical ? Math.sin(time * 2.2 + node.home.x) * 0.06 : 0;
      node.group.position.y = node.introY;
      node.lift.position.y = node.raise + node.hover * 0.28 + bob;
      node.capMat.color.copy(node.color);
      node.capMat.emissive.copy(node.color);
      node.capMat.emissiveIntensity = critical ? 0.45 + Math.sin(time * 4.2) * 0.25 : node.status === 'proposed' ? 0.4 + Math.sin(time * 2.4) * 0.15 : 0.1;
      node.beamMat.opacity += ((critical ? 0.12 : 0) - node.beamMat.opacity) * Math.min(1, dt * 4);
      node.beam.scale.set(1, 1 + Math.sin(time * 3) * 0.04, 1);
      node.pulses.forEach((ring, index) => {
        const phase = (time * 0.62 + index * 0.5) % 1;
        ring.scale.setScalar(0.55 + phase * 1.25);
        ring.material.opacity = critical ? (1 - phase) * 0.75 : 0;
      });
      node.halo.rotation.y = time * 0.35;
    });

    this.edges.forEach((edge) => {
      edge.mesh.geometry.setDrawRange(0, Math.floor((edge.total * edge.draw) / 6) * 6);
      if (edge.kind === 'reroute') {
        edge.mesh.visible = edge.draw > 0.002;
        edge.material.emissiveIntensity = this.state?.reroute === 'proposed' ? 0.35 + Math.sin(time * 3) * 0.3 : 0.25;
      } else {
        const t = THEMES[this.theme];
        this.tmpColor.set(t.edge).lerp(COLORS.coral, edge.alert);
        edge.material.color.copy(this.tmpColor);
        edge.material.emissive.copy(COLORS.coral).multiplyScalar(edge.alert * (0.35 + Math.sin(time * 4) * 0.2));
      }
    });

    this.updateParticles(dt, time);
    this.updateEffects(dt, time);
    this.updateMascots(dt, time);

    this.renderer.render(this.scene, this.camera);
    this.labels.render(this.scene, this.camera);
  }

  updateParticles(dt, time) {
    const state = this.state || this.fixture.mapStates[0];
    this.particleState.forEach((p, index) => {
      if (!p.alive) {
        p.wait -= dt;
        if (p.wait <= 0) this.spawn(p);
        if (!p.alive) {
          this.tmpMatrix.makeScale(0, 0, 0);
          this.particles.setMatrixAt(index, this.tmpMatrix);
          return;
        }
      }
      const from = p.path[p.seg];
      const to = p.path[p.seg + 1];
      const edge = this.edges.get(`${from}>${to}`);
      if (!edge || (edge.kind !== 'reroute' && edge.draw < 0.98)) {
        p.alive = false; p.wait = 0.4; return;
      }
      const failingSeg = state.failing && p.flow.failing && from === 'gb' && to === 'bankx';
      if (p.dying > 0) {
        p.dying -= dt * 2.6;
        if (p.dying <= 0) { p.alive = false; p.wait = Math.random() * 1.2; }
      } else {
        p.t += (p.speed * dt) / edge.length;
        if (failingSeg && p.t >= 0.9) {
          p.t = 0.9;
          p.dying = 1;
          this.spark(edge.curve.getPointAt(0.95), time);
        } else if (p.t >= 1) {
          p.seg += 1;
          p.t = 0;
          if (p.seg >= p.path.length - 1) { p.alive = false; p.wait = 0.3 + Math.random() * 2.4; }
        }
      }
      if (!p.alive) {
        this.tmpMatrix.makeScale(0, 0, 0);
        this.particles.setMatrixAt(index, this.tmpMatrix);
        return;
      }
      edge.curve.getPointAt(Math.min(1, p.t), this.tmpPos);
      this.tmpPos.y += 0.04;
      const scale = (p.flow.probe ? 1.15 : 1) * (p.dying > 0 ? p.dying : 1) * Math.min(1, p.t * 6 + (p.seg > 0 ? 1 : 0));
      this.tmpScale.setScalar(scale);
      this.tmpMatrix.compose(this.tmpPos, this.tmpQuat, this.tmpScale);
      this.particles.setMatrixAt(index, this.tmpMatrix);
      if (p.flow.probe) this.tmpColor.copy(COLORS.violet);
      else if (failingSeg) this.tmpColor.copy(COLORS.teal).lerp(COLORS.coral, Math.min(1, p.t * 1.6));
      else this.tmpColor.copy(COLORS.teal);
      this.particles.setColorAt(index, this.tmpColor);
    });
    this.particles.instanceMatrix.needsUpdate = true;
    if (this.particles.instanceColor) this.particles.instanceColor.needsUpdate = true;
  }

  spark(position, time) {
    if (time - this.lastSpark < 0.18) return;
    this.lastSpark = time;
    const spark = this.sparks[this.sparkIndex++ % this.sparks.length];
    spark.mesh.position.copy(position);
    spark.life = 0;
  }

  updateEffects(dt, time) {
    this.sparks.forEach((spark) => {
      spark.life = Math.min(1, spark.life + dt * 1.8);
      spark.mesh.quaternion.copy(this.camera.quaternion);
      spark.mesh.scale.setScalar(0.3 + spark.life * 1.1);
      spark.mat.opacity = (1 - spark.life) * 0.9;
    });
    const phase = (time * 0.38) % 1;
    this.blast.scale.setScalar(1 + phase * 12);
    this.blastMat.opacity = this.blastLevel * (1 - phase) * 0.55;
  }

  updateMascots(dt, time) {
    Object.values(this.mascots).forEach((mascot) => {
      const size = Math.max(0.0001, mascot.size * 1.75);
      const frames = mascot.frames[mascot.mode] || Object.values(mascot.frames)[0];
      const period = mascot.mode === 'working' ? 0.42 : 0.7;
      const frame = frames[Math.floor(time / period) % frames.length];
      if (mascot.material.map !== frame) mascot.material.map = frame;
      if (mascot.ride) {
        const edges = [this.edges.get('router>gb'), this.edges.get('gb>bankx')];
        const loop = (time * 0.12) % 2;
        const edge = edges[Math.floor(loop)];
        edge.curve.getPointAt(loop % 1, this.tmpPos);
        mascot.group.position.lerp(this.tmpPos.setY(0), Math.min(1, dt * 6));
        mascot.sprite.position.y = edge.curve.getPointAt(loop % 1).y + 0.15;
      } else {
        const bob = mascot.mode === 'result' ? Math.abs(Math.sin(time * 2.4)) * 0.22 : Math.sin(time * 1.8) * 0.06;
        mascot.sprite.position.y = 0.05 + bob;
      }
      mascot.sprite.scale.setScalar(size);
      mascot.shadow.scale.setScalar(Math.max(0.0001, mascot.size));
      mascot.shadow.material.opacity = mascot.size * 0.55;
      mascot.shadow.position.y = 0.02;
    });
  }
}
