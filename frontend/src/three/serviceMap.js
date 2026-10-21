import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
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
  light: { board: '#fffdfb', plinth: '#e3d5cc', lane: '#f6efe9', node: '#fffdfb', nodeBase: '#eadfd8', grid: '#dccfc6', edge: '#d3c2b7', crowd: '#d8cbc2', district: '#f8f1ec' },
  dark: { board: '#2a211e', plinth: '#120d0c', lane: '#241c19', node: '#3a2e2a', nodeBase: '#1f1816', grid: '#4a3b35', edge: '#5c4a42', crowd: '#5a4a43', district: '#302622' },
};

const COLORS = {
  teal: new THREE.Color('#1ea896'),
  coral: new THREE.Color('#ff715b'),
  violet: new THREE.Color('#8178d8'),
  wine: new THREE.Color('#b01352'),
  shirt: new THREE.Color('#d9432f'),
  screenFail: new THREE.Color('#ff4d6d'),
  screenWine: new THREE.Color('#ff2e7e'),
  screenOk: new THREE.Color('#5ff5dc'),
};

const LANE_SHAPES = { channels: 'tile', services: 'tower', routes: 'hex', partners: 'vault' };
const SHAPE_HEIGHT = { tile: 0.5, tower: 0.92, hex: 0.72, vault: 1 };
const S = 1.22;
const VIEW_HEIGHT = 30;
const CAMERA_DISTANCE = 90;
const VIEW_DIRECTION = new THREE.Vector3(0, 0.62, 0.78).normalize();
const TWIN_LIFT = 8;
const PARTICLE_COUNT = 150;
const CROWD = { spacing: 0.34, figure: 0.68, rightEdge: -18.4 };

// Plain concentric rings of seats around the epicentre, filled from the centre outward.
function ringSeats(count, spacing) {
  const seats = [{ x: 0, z: 0, r: 0 }];
  for (let ring = 1; seats.length < count; ring += 1) {
    const r = ring * spacing;
    const slots = Math.min(Math.round((2 * Math.PI * r) / spacing), count - seats.length);
    const offset = ring * 0.37;
    for (let i = 0; i < slots; i += 1) {
      const angle = offset + (i / slots) * Math.PI * 2;
      seats.push({ x: Math.cos(angle) * r, z: Math.sin(angle) * r, r });
    }
  }
  return seats;
}
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const format = new Intl.NumberFormat('en-NG');

export function webglAvailable() {
  try {
    const canvas = document.createElement('canvas');
    return Boolean(window.WebGLRenderingContext && (canvas.getContext('webgl2') || canvas.getContext('webgl')));
  } catch {
    return false;
  }
}

// Small seeded generator so the crowd looks identical on every demo run.
function seeded(seed) {
  let a = seed;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function toWorld(node) {
  return new THREE.Vector3((node.x - 48.5) * 0.4, 0, (node.y - 53) * 0.36);
}

function canvasTexture(size, draw) {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  draw(canvas.getContext('2d'), size);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 4;
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

function iconTexture(icon) {
  return canvasTexture(256, (ctx, size) => {
    ctx.fillStyle = '#ffffff';
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = 16;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.shadowColor = 'rgba(0,0,0,.18)';
    ctx.shadowOffsetY = 6;
    const c = size / 2;
    if (icon === 'phone') {
      ctx.beginPath();
      ctx.roundRect(c - 46, c - 76, 92, 152, 22);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(c, c + 52, 8, 0, Math.PI * 2);
      ctx.fill();
    } else if (icon === 'route') {
      ctx.beginPath();
      ctx.moveTo(c - 80, c);
      ctx.lineTo(c - 20, c);
      ctx.moveTo(c - 20, c);
      ctx.bezierCurveTo(c + 10, c, c + 20, c - 56, c + 70, c - 56);
      ctx.moveTo(c - 20, c);
      ctx.bezierCurveTo(c + 10, c, c + 20, c + 56, c + 70, c + 56);
      ctx.moveTo(c - 20, c);
      ctx.lineTo(c + 70, c);
      ctx.stroke();
    } else {
      const fontSize = icon.length > 2 ? (icon.length > 3 ? 74 : 92) : 150;
      ctx.font = `800 ${fontSize}px "Segoe UI", Arial, sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(icon, c, c + fontSize * 0.05);
    }
  });
}

function shapeGeometry(shape, scale = 1) {
  const h = SHAPE_HEIGHT[shape];
  const k = S * scale;
  if (shape === 'hex') return new THREE.CylinderGeometry(0.98 * k, 1.04 * k, h, 6, 1);
  if (shape === 'vault') return new THREE.CylinderGeometry(0.86 * k, 0.92 * k, h, 48, 1);
  if (shape === 'tower') return new RoundedBoxGeometry(2.2 * k, h, 1.45 * k, 4, 0.22);
  return new RoundedBoxGeometry(1.9 * k, h, 1.25 * k, 4, 0.2);
}

function capGeometry(shape) {
  if (shape === 'hex') return new THREE.CylinderGeometry(0.78 * S, 0.82 * S, 0.1, 6, 1);
  if (shape === 'vault') return new THREE.CylinderGeometry(0.68 * S, 0.7 * S, 0.1, 48, 1);
  if (shape === 'tower') return new RoundedBoxGeometry(1.86 * S, 0.1, 1.1 * S, 2, 0.05);
  return new RoundedBoxGeometry(1.58 * S, 0.1, 0.92 * S, 2, 0.05);
}

const ICON_SIZE = { hex: 1.3, vault: 1.15, tower: 1.15, tile: 1.05 };

function dashedRing(radius, color) {
  const group = new THREE.Group();
  const material = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0, side: THREE.DoubleSide, depthWrite: false });
  const dashes = 20;
  for (let i = 0; i < dashes; i += 1) {
    const arc = new THREE.Mesh(new THREE.RingGeometry(radius, radius + 0.1, 6, 1, (i / dashes) * Math.PI * 2, (Math.PI * 2 / dashes) * 0.55), material);
    arc.rotation.x = -Math.PI / 2;
    group.add(arc);
  }
  group.userData.material = material;
  return group;
}

function arc(start, end, lift) {
  const dir = end.clone().sub(start).multiplyScalar(0.28);
  return new THREE.CubicBezierCurve3(
    start,
    start.clone().add(dir).add(new THREE.Vector3(0, lift, 0)),
    end.clone().sub(dir).add(new THREE.Vector3(0, lift, 0)),
    end,
  );
}

function label(className, html, center = [0.5, 0.5]) {
  const el = document.createElement('div');
  el.className = className;
  el.innerHTML = html;
  const object = new CSS2DObject(el);
  object.center.set(...center);
  return object;
}

const LOCK_ICON = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>';
const UNLOCK_ICON = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 7.5-2"/></svg>';
const WARN_ICON = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 2 20h20L12 3Z"/><path d="M12 10v4M12 17h.01"/></svg>';

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
    this.userMoved = false;
    this.focusTween = null;
    this.frameTimes = [];
    this.insets = { top: 0, right: 0, bottom: 0, left: 0 };
    this.twinLevel = 0;
    this.blastLevel = 0;

    this.host = document.createElement('div');
    this.host.className = 'map3d-host';

    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.NeutralToneMapping;
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

    this.camera = new THREE.OrthographicCamera(-24, 24, 15, -15, 0.1, 400);
    this.camera.position.copy(VIEW_DIRECTION).multiplyScalar(CAMERA_DISTANCE);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.07;
    this.controls.enablePan = false;
    this.controls.minPolarAngle = 0.3;
    this.controls.maxPolarAngle = 1.22;
    this.controls.minZoom = 0.2;
    this.controls.maxZoom = 6;
    this.controls.rotateSpeed = 0.6;
    this.controls.addEventListener('start', () => { this.userMoved = true; this.focusTween?.kill(); });

    this.clock = new THREE.Clock();
    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2(9, 9);
    this.tmpMatrix = new THREE.Matrix4();
    this.tmpQuat = new THREE.Quaternion();
    this.tmpScale = new THREE.Vector3();
    this.tmpPos = new THREE.Vector3();
    this.tmpColor = new THREE.Color();

    this.layout();
    this.buildLights();
    this.buildBoard();
    this.buildCrowd();
    this.buildNodes();
    this.buildEdges();
    this.buildTwin();
    this.buildParticles();
    this.buildStoryMarkers();
    this.buildMascots();
    this.bindPointer();
    this.buildDeclutter();

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(this.host);
    document.addEventListener('visibilitychange', () => this.updateLoop());
  }

  layout() {
    const seats = ringSeats(this.fixture.impact.affected, CROWD.spacing);
    const radius = seats[seats.length - 1].r;
    const centerX = CROWD.rightEdge - radius;
    this.field = { ...CROWD, seats, radius, centerX };
    this.bounds = { minX: centerX - radius - 2.2, maxX: 18.6, minZ: -12.4, maxZ: 12.4 };
    this.boardCenter = new THREE.Vector3((this.bounds.minX + this.bounds.maxX) / 2, 0, 0);
  }

  buildLights() {
    this.scene.add(new THREE.HemisphereLight('#fffaf5', '#e9ddd5', 1.1));
    const key = new THREE.DirectionalLight('#fff4ea', 2.1);
    key.position.set(-14, 26, 16);
    key.target.position.copy(this.boardCenter);
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    Object.assign(key.shadow.camera, { left: -30, right: 30, top: 20, bottom: -20, near: 1, far: 90 });
    key.shadow.bias = -0.0004;
    key.shadow.normalBias = 0.03;
    this.scene.add(key, key.target);
    const rim = new THREE.DirectionalLight('#ffd8cf', 0.65);
    rim.position.set(16, 8, -16);
    this.scene.add(rim);
  }

  buildBoard() {
    const t = THEMES[this.theme];
    const { minX, maxX, minZ, maxZ } = this.bounds;
    const width = maxX - minX;
    const depth = maxZ - minZ;
    this.board = new THREE.Group();
    this.board.position.copy(this.boardCenter);
    this.scene.add(this.board);

    this.boardMat = new THREE.MeshStandardMaterial({ color: t.board, roughness: 0.92 });
    this.plinthMat = new THREE.MeshStandardMaterial({ color: t.plinth, roughness: 0.95 });
    const top = new THREE.Mesh(new RoundedBoxGeometry(width, 0.6, depth, 6, 0.5), this.boardMat);
    top.position.y = -0.3;
    top.receiveShadow = true;
    const plinth = new THREE.Mesh(new RoundedBoxGeometry(width, 0.6, depth, 6, 0.5), this.plinthMat);
    plinth.position.y = -0.68;
    plinth.receiveShadow = true;
    this.board.add(plinth, top);

    this.gridTexture = canvasTexture(256, (ctx, size) => {
      ctx.fillStyle = '#ffffff';
      for (let x = 16; x < size; x += 32) for (let y = 16; y < size; y += 32) {
        ctx.beginPath(); ctx.arc(x, y, 1.7, 0, Math.PI * 2); ctx.fill();
      }
    });
    this.gridTexture.wrapS = this.gridTexture.wrapT = THREE.RepeatWrapping;
    this.gridTexture.repeat.set(width / 2.1, depth / 2.1);
    this.gridMat = new THREE.MeshBasicMaterial({ map: this.gridTexture, color: t.grid, transparent: true, depthWrite: false });
    const grid = new THREE.Mesh(new THREE.PlaneGeometry(width - 1, depth - 1), this.gridMat);
    grid.rotation.x = -Math.PI / 2;
    grid.position.y = 0.006;
    this.board.add(grid);

    this.laneMat = new THREE.MeshStandardMaterial({ color: t.lane, roughness: 1 });
    const byId = Object.fromEntries(this.fixture.nodes.map((node) => [node.id, node]));
    this.fixture.lanes.forEach((lane, index) => {
      const cx = lane.nodes.length
        ? lane.nodes.map((id) => toWorld(byId[id]).x).reduce((a, b) => a + b, 0) / lane.nodes.length
        : this.field.centerX;
      if (index % 2 === 1) {
        const band = new THREE.Mesh(new RoundedBoxGeometry(4.6, 0.02, depth - 1.6, 2, 0.01), this.laneMat);
        band.position.set(cx - this.boardCenter.x, 0.012, 0);
        band.receiveShadow = true;
        this.board.add(band);
      }
      const lane3d = label('map3d-lane', lane.label);
      lane3d.position.set(cx - this.boardCenter.x, -0.24, maxZ + 0.35);
      this.board.add(lane3d);
      (this.laneLabels ||= []).push(lane3d.element);
    });
  }

  buildCrowd() {
    const t = THEMES[this.theme];
    const { centerX, radius, seats, figure } = this.field;
    const { affected, complained } = this.fixture.impact;
    const random = seeded(1109);

    this.districtMat = new THREE.MeshStandardMaterial({ color: t.district, roughness: 0.9 });
    const district = new THREE.Mesh(new THREE.CylinderGeometry(radius + 0.9, radius + 1, 0.16, 96), this.districtMat);
    district.position.set(centerX, 0.08, 0);
    district.receiveShadow = true;
    district.userData.nodeId = 'crowd';
    this.district = district;
    this.scene.add(district);

    // The spreading failure: a coral ground disc with a bright front edge.
    this.frontMat = new THREE.MeshBasicMaterial({ color: STATUS_COLORS.critical, transparent: true, opacity: 0, depthWrite: false });
    this.frontDisc = new THREE.Mesh(new THREE.CircleGeometry(1, 96), this.frontMat);
    this.frontEdgeMat = new THREE.MeshBasicMaterial({ color: STATUS_COLORS.critical, transparent: true, opacity: 0, depthWrite: false, side: THREE.DoubleSide });
    this.frontEdge = new THREE.Mesh(new THREE.RingGeometry(0.975, 1, 128), this.frontEdgeMat);
    [this.frontDisc, this.frontEdge].forEach((mesh, index) => {
      mesh.rotation.x = -Math.PI / 2;
      mesh.position.set(centerX, 0.17 + index * 0.005, 0);
      mesh.scale.setScalar(0.001);
      this.scene.add(mesh);
    });
    this.blastRings = [1 / 3, 2 / 3, 1].map((share) => {
      const ring = new THREE.Mesh(new THREE.RingGeometry(0.99, 1, 128), new THREE.MeshBasicMaterial({ color: STATUS_COLORS.critical, transparent: true, opacity: 0, depthWrite: false, side: THREE.DoubleSide }));
      ring.rotation.x = -Math.PI / 2;
      ring.position.set(centerX, 0.18, 0);
      ring.scale.setScalar(radius * share + 0.2);
      this.scene.add(ring);
      return ring;
    });
    this.front = 0;

    // A person holding a phone: shirt and arms take the status colour, heads keep a
    // skin tone, and the phone screen glows.
    const torso = new THREE.CapsuleGeometry(0.11, 0.3, 4, 10).translate(0, 0.26, 0);
    const arm = (side) => new THREE.CapsuleGeometry(0.032, 0.15, 3, 6).rotateX(-1.05).translate(side * 0.1, 0.4, 0.085);
    const shirtGeometry = mergeGeometries([torso, arm(-1), arm(1)]).scale(figure, figure, figure);
    const headGeometry = new THREE.SphereGeometry(0.078, 14, 12).translate(0, 0.62, 0).scale(figure, figure, figure);
    const phoneGeometry = new THREE.BoxGeometry(0.13, 0.19, 0.025).rotateX(-0.45).translate(0, 0.44, 0.19).scale(figure, figure, figure);
    // Darker base colours keep shirt and skin tones saturated under the bright studio light.
    this.crowdMat = new THREE.MeshStandardMaterial({ color: '#b8b8b8', roughness: 0.65, envMapIntensity: 0.4 });
    this.crowd = new THREE.InstancedMesh(shirtGeometry, this.crowdMat, affected);
    this.heads = new THREE.InstancedMesh(headGeometry, new THREE.MeshStandardMaterial({ color: '#d0d0d0', roughness: 0.7, envMapIntensity: 0.4 }), affected);
    this.phones = new THREE.InstancedMesh(phoneGeometry, new THREE.MeshBasicMaterial({ toneMapped: false }), affected);
    this.crowd.castShadow = true;
    this.heads.castShadow = true;
    [this.crowd, this.heads, this.phones].forEach((mesh) => mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage));
    this.crowdNeutral = new THREE.Color(t.crowd);
    this.screenIdle = new THREE.Color('#8ce8da');
    const skins = ['#5c3a21', '#7a4a2a', '#8d5524', '#a86b3c', '#c68642', '#e0ac69'].map((hex) => new THREE.Color(hex));

    const complainerSlots = new Set();
    while (complainerSlots.size < complained) complainerSlots.add(Math.floor(random() * affected * 0.3));
    this.people = [];
    for (let i = 0; i < affected; i += 1) {
      const seat = seats[i];
      const complainer = complainerSlots.has(i);
      this.people.push({
        x: centerX + seat.x,
        z: seat.z,
        yaw: (random() - 0.5) * 0.8,
        threshold: Math.min(0.999, seat.r / (radius + 0.01)),
        complainer,
        color: this.crowdNeutral.clone(),
        screen: this.screenIdle.clone(),
        lift: 0,
        phase: random() * Math.PI * 2,
      });
      this.crowd.setColorAt(i, this.crowdNeutral);
      this.heads.setColorAt(i, skins[Math.floor(random() * skins.length)]);
      this.phones.setColorAt(i, this.screenIdle);
    }
    this.scene.add(this.crowd, this.heads, this.phones);
    this.crowdTarget = { affected: 0, complainers: false, notified: false };

    this.complainerRings = [...complainerSlots].map((index) => {
      const person = this.people[index];
      const ring = new THREE.Mesh(new THREE.RingGeometry(0.2, 0.25, 24), new THREE.MeshBasicMaterial({ color: COLORS.wine, transparent: true, opacity: 0, depthWrite: false, side: THREE.DoubleSide }));
      ring.rotation.x = -Math.PI / 2;
      ring.position.set(person.x, 0.19, person.z);
      this.scene.add(ring);
      return ring;
    });

    this.crowdLabel = label('map3d-crowd', '', [0.5, 1]);
    this.crowdLabel.position.set(centerX, 0.2, -radius - 1.1);
    this.scene.add(this.crowdLabel);

    const quotes = this.fixture.rawComplaints;
    const spots = [[-0.6, -0.4], [0.15, -0.2], [-0.55, 0.3], [0.1, 0.45]];
    this.bubbles = quotes.map((text, index) => {
      const bubble = label('map3d-bubble', `<div class="map3d-card">${text}</div>`, [0.08, 1]);
      bubble.element.style.setProperty('--delay', `${index * 0.45}s`);
      const [sx, sz] = spots[index % spots.length];
      bubble.position.set(centerX + sx * radius, 0.9, sz * radius);
      this.scene.add(bubble);
      return bubble;
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

      const base = new THREE.Mesh(shapeGeometry(shape, 1.07), this.nodeBaseMat);
      base.scale.y = 0.35;
      base.position.y = height * 0.175;
      base.castShadow = true;
      base.receiveShadow = true;
      const body = new THREE.Mesh(shapeGeometry(shape), this.nodeMat);
      body.position.y = height / 2 + 0.06;
      body.castShadow = true;
      body.receiveShadow = true;
      const capMat = new THREE.MeshStandardMaterial({ color: STATUS_COLORS.healthy, emissive: STATUS_COLORS.healthy, emissiveIntensity: 0.1, roughness: 0.35 });
      const cap = new THREE.Mesh(capGeometry(shape), capMat);
      cap.position.y = height + 0.1;
      cap.castShadow = true;
      const icon = new THREE.Mesh(
        new THREE.PlaneGeometry(ICON_SIZE[shape], ICON_SIZE[shape]),
        new THREE.MeshBasicMaterial({ map: iconTexture(this.fixture.nodeIcons[id]), transparent: true, depthWrite: false, toneMapped: false, polygonOffset: true, polygonOffsetFactor: -2 }),
      );
      icon.rotation.x = -Math.PI / 2;
      icon.position.y = height + 0.16;
      lift.add(base, body, cap, icon);

      const beamMat = new THREE.MeshBasicMaterial({ color: STATUS_COLORS.critical, alphaMap: this.beamTexture, transparent: true, opacity: 0, side: THREE.DoubleSide, depthWrite: false });
      const beam = new THREE.Mesh(new THREE.CylinderGeometry(1.0 * S, 1.15 * S, 3.4, 40, 1, true), beamMat);
      beam.position.y = height + 1.7;
      lift.add(beam);

      const pulses = [0, 1].map(() => {
        const mat = new THREE.MeshBasicMaterial({ color: STATUS_COLORS.critical, map: this.ringTexture, transparent: true, opacity: 0, depthWrite: false });
        const ring = new THREE.Mesh(new THREE.PlaneGeometry(4.2, 4.2), mat);
        ring.rotation.x = -Math.PI / 2;
        ring.position.y = 0.03;
        group.add(ring);
        return ring;
      });

      const halo = dashedRing(1.85, STATUS_COLORS.suspect);
      halo.position.y = 0.04;
      group.add(halo);

      const tag = label('map3d-label', `<div class="map3d-card"><span class="map3d-dot"></span><strong>${data.name}</strong><small></small><em hidden></em><div class="map3d-hyp" hidden></div></div>`, [0.5, 1]);
      tag.position.y = height + 0.85;
      lift.add(tag);

      [base, body, cap].forEach((mesh) => { mesh.userData.nodeId = id; });
      this.scene.add(group);
      this.nodes.set(id, {
        id, data, shape, height, group, lift, capMat, beam, beamMat, pulses, halo, el: tag.element, home,
        status: 'healthy', color: new THREE.Color(STATUS_COLORS.healthy), raise: 0, hover: 0, introY: 0,
        pickables: [base, body, cap],
      });
    }));
  }

  buildEdges() {
    const t = THEMES[this.theme];
    this.edges = new Map();
    this.edgeMat = new THREE.MeshStandardMaterial({ color: t.edge, roughness: 0.6 });
    this.fixture.edges.forEach(([from, to, kind]) => {
      const b = this.nodes.get(to);
      const end = b.home.clone().setY(b.height + 0.18);
      const start = from === 'crowd'
        ? new THREE.Vector3(this.field.centerX + Math.sqrt(Math.max(0, this.field.radius ** 2 - b.home.z ** 2)) + 0.4, 0.3, b.home.z)
        : this.nodes.get(from).home.clone().setY(this.nodes.get(from).height + 0.18);
      const dist = start.distanceTo(end);
      const curve = arc(start, end, 0.6 + dist * 0.12 + (kind === 'reroute' ? 1.4 : 0));
      const geometry = new THREE.TubeGeometry(curve, 80, kind === 'reroute' ? 0.09 : 0.065, 10, false);
      const material = kind === 'reroute'
        ? new THREE.MeshStandardMaterial({ color: STATUS_COLORS.proposed, emissive: STATUS_COLORS.proposed, emissiveIntensity: 0.35, roughness: 0.4 })
        : this.edgeMat.clone();
      material.transparent = true;
      const mesh = new THREE.Mesh(geometry, material);
      mesh.castShadow = true;
      const total = geometry.index.count;
      geometry.setDrawRange(0, kind === 'reroute' ? 0 : total);
      this.scene.add(mesh);
      this.edges.set(`${from}>${to}`, { from, to, kind, curve, length: curve.getLength(), mesh, material, total, draw: kind === 'reroute' ? 0 : 1, alert: 0, bug: from === 'gb' && to === 'bankx' });
    });
  }

  buildTwin() {
    this.twin = new THREE.Group();
    this.scene.add(this.twin);
    this.twinMats = {
      body: new THREE.MeshStandardMaterial({ color: '#a39cf3', emissive: '#8178d8', emissiveIntensity: 0.35, roughness: 0.3, transparent: true, opacity: 0, depthWrite: false }),
      line: new THREE.LineBasicMaterial({ color: '#6c62d6', transparent: true, opacity: 0 }),
      tube: new THREE.MeshBasicMaterial({ color: '#8f86ea', transparent: true, opacity: 0, depthWrite: false }),
      drop: new THREE.LineDashedMaterial({ color: '#8178d8', dashSize: 0.22, gapSize: 0.18, transparent: true, opacity: 0 }),
      deck: new THREE.MeshBasicMaterial({ color: '#8178d8', map: radialTexture([[0, 0.28], [0.75, 0.12], [1, 0]]), transparent: true, opacity: 0, depthWrite: false }),
    };
    const ids = [...new Set(this.fixture.twinEdges.flat())];
    this.twinNodes = new Map();
    const tint = { gb: '#ff8f7d', bankx: '#ff8f7d', gc: '#5fd3c2', bankz: '#5fd3c2' };
    this.twinTinted = [];
    ids.forEach((id) => {
      const node = this.nodes.get(id);
      const top = node.home.clone().setY(TWIN_LIFT);
      const geometry = shapeGeometry(node.shape, 0.92);
      const material = tint[id] ? this.twinMats.body.clone() : this.twinMats.body;
      if (tint[id]) {
        material.color.set(tint[id]);
        material.emissive.set(tint[id]);
        this.twinTinted.push(material);
      }
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.copy(top).setY(TWIN_LIFT + node.height / 2);
      const outline = new THREE.LineSegments(new THREE.EdgesGeometry(geometry, 30), this.twinMats.line);
      outline.position.copy(mesh.position);
      const drop = new THREE.Line(new THREE.BufferGeometry().setFromPoints([node.home.clone().setY(node.height + 1.6), top.clone()]), this.twinMats.drop);
      drop.computeLineDistances();
      this.twin.add(mesh, outline, drop);
      this.twinNodes.set(id, { home: top, height: node.height });
    });
    const xs = ids.map((id) => this.nodes.get(id).home.x);
    const zs = ids.map((id) => this.nodes.get(id).home.z);
    const deck = new THREE.Mesh(new THREE.PlaneGeometry(Math.max(...xs) - Math.min(...xs) + 7, Math.max(...zs) - Math.min(...zs) + 6), this.twinMats.deck);
    deck.rotation.x = -Math.PI / 2;
    deck.position.set((Math.max(...xs) + Math.min(...xs)) / 2, TWIN_LIFT - 0.05, (Math.max(...zs) + Math.min(...zs)) / 2);
    this.twin.add(deck);

    this.fixture.twinEdges.forEach(([from, to]) => {
      const a = this.twinNodes.get(from);
      const b = this.twinNodes.get(to);
      const curve = arc(a.home.clone().setY(TWIN_LIFT + a.height + 0.1), b.home.clone().setY(TWIN_LIFT + b.height + 0.1), 0.9);
      const mesh = new THREE.Mesh(new THREE.TubeGeometry(curve, 60, 0.055, 8, false), this.twinMats.tube);
      this.twin.add(mesh);
      this.edges.set(`~${from}>~${to}`, { from: `~${from}`, to: `~${to}`, kind: 'twin', curve, length: curve.getLength(), mesh, total: 0, draw: 0, alert: 0, bug: from === 'gb' && to === 'bankx' });
    });

    const tests = Object.fromEntries(this.fixture.tests.map((test) => [test.id, test]));
    const deckLabel = label('map3d-twin-title', `<div class="map3d-card"><b>Digital twin</b><span>Synthetic transfers only · no customer money</span></div>`, [0.5, 1]);
    deckLabel.center.set(1, 0.5);
    deckLabel.position.copy(this.twinNodes.get('router').home).add(new THREE.Vector3(-2.2, 1.2, 0));
    const fail = label('map3d-result fail', `<div class="map3d-card"><i>✕</i><div><strong>Through Gateway B</strong><span>${tests['T-01'].runs} reproduced · ack timeout</span></div></div>`, [1, 0.5]);
    fail.position.copy(this.twinNodes.get('gb').home).add(new THREE.Vector3(-1.8, 0.6, 0));
    const pass = label('map3d-result pass', `<div class="map3d-card"><i>✓</i><div><strong>Gateway C control</strong><span>${tests['T-02'].runs} reproduced · healthy</span></div></div>`, [1, 0.5]);
    pass.position.copy(this.twinNodes.get('gc').home).add(new THREE.Vector3(-1.8, 0.6, 0));
    this.twinLabels = [deckLabel, fail, pass];
    this.twinLabels.forEach((item) => this.twin.add(item));
  }

  buildParticles() {
    const geometry = new THREE.SphereGeometry(0.14, 14, 10);
    const material = new THREE.MeshBasicMaterial({ toneMapped: false });
    this.particles = new THREE.InstancedMesh(geometry, material, PARTICLE_COUNT);
    this.particles.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    this.particles.frustumCulled = false;
    this.particleState = [];
    for (let i = 0; i < PARTICLE_COUNT; i += 1) {
      this.particles.setColorAt(i, COLORS.teal);
      this.particleState.push({ alive: false, wait: Math.random() * 3 });
    }
    this.scene.add(this.particles);
  }

  buildStoryMarkers() {
    this.sparks = [0, 1, 2, 3].map(() => {
      const mat = new THREE.MeshBasicMaterial({ color: STATUS_COLORS.critical, map: this.ringTexture, transparent: true, opacity: 0, depthWrite: false });
      const mesh = new THREE.Mesh(new THREE.PlaneGeometry(1.8, 1.8), mat);
      this.scene.add(mesh);
      return { mesh, mat, life: 1 };
    });
    this.sparkIndex = 0;
    this.lastSpark = 0;

    const bug = this.edges.get('gb>bankx');
    const breakPoint = bug.curve.getPointAt(0.88);
    this.breakRing = new THREE.Mesh(new THREE.TorusGeometry(0.3, 0.07, 10, 32), new THREE.MeshBasicMaterial({ color: STATUS_COLORS.critical, transparent: true, opacity: 0, toneMapped: false }));
    this.breakRing.position.copy(breakPoint);
    this.breakRing.lookAt(breakPoint.clone().add(bug.curve.getTangentAt(0.88)));
    this.scene.add(this.breakRing);
    this.bugMarker = label('map3d-bug', '', [0.5, 0]);
    this.bugMarker.position.copy(bug.curve.getPointAt(0.5)).setY(0).add(new THREE.Vector3(0, 0, 1.3));
    this.scene.add(this.bugMarker);

    const blastTexture = radialTexture([[0, 0.05], [0.7, 0.22], [0.93, 0.9], [1, 0]]);
    this.blastMat = new THREE.MeshBasicMaterial({ color: STATUS_COLORS.critical, map: blastTexture, transparent: true, opacity: 0, depthWrite: false });
    this.blast = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), this.blastMat);
    this.blast.rotation.x = -Math.PI / 2;
    this.blast.position.set(this.field.centerX, 0.2, 0);
    this.scene.add(this.blast);

    const reroute = this.edges.get('gc>bankx');
    this.lock = label('map3d-lock', '', [1, 0.5]);
    this.lock.position.copy(reroute.curve.getPointAt(0.5)).add(new THREE.Vector3(-0.6, 0, 0));
    this.scene.add(this.lock);

    this.release = label('map3d-badge', `<div class="map3d-card"><b>App v4.2</b> released at 10:42</div>`, [0.5, 0]);
    this.release.position.copy(this.nodes.get('app').home).add(new THREE.Vector3(0, 0, 1.7));
    this.scene.add(this.release);
  }

  buildMascots() {
    const loader = new THREE.TextureLoader();
    const load = (agent, frame) => {
      const texture = loader.load(`/assets/agents/${agent}/agent-${frame}.png`);
      texture.colorSpace = THREE.SRGBColorSpace;
      return texture;
    };
    const shadowTexture = radialTexture([[0, 0.5], [0.5, 0.22], [1, 0]]);
    const colors = { observability: '#1EA896', ghost: '#8178D8', blast: '#F2A65A', resolution: '#FF715B' };
    const names = { observability: 'Observability', ghost: 'Ghost Test', blast: 'Blast Radius', resolution: 'Resolution' };
    // Each agent stands at its own post on the open front edge of the board, clear of the labels.
    const front = this.bounds.maxZ - 1.3;
    const posts = {
      observability: new THREE.Vector3(-11.8, 0, front),
      ghost: new THREE.Vector3(3.9, 0, front),
      resolution: new THREE.Vector3(11.8, 0, front),
      blast: new THREE.Vector3(this.field.centerX + 5.4, 0, this.field.radius + 2.6),
    };
    const make = (agent) => {
      const frames = {
        listening: ['listen-a', 'listen-b'].map((frame) => load(agent, frame)),
        working: ['work-a', 'work-b'].map((frame) => load(agent, frame)),
        result: ['result'].map((frame) => load(agent, frame)),
      };
      const material = new THREE.SpriteMaterial({ map: frames.listening[0], transparent: true, depthWrite: false, depthTest: false });
      const sprite = new THREE.Sprite(material);
      sprite.center.set(0.5, 0.05);
      sprite.renderOrder = 10;
      sprite.scale.setScalar(0.001);
      const shadow = new THREE.Mesh(new THREE.PlaneGeometry(1.8, 0.95), new THREE.MeshBasicMaterial({ color: '#523f38', map: shadowTexture, transparent: true, opacity: 0, depthWrite: false }));
      shadow.rotation.x = -Math.PI / 2;
      const ring = new THREE.Mesh(new THREE.RingGeometry(0.85, 1, 48), new THREE.MeshBasicMaterial({ color: colors[agent], transparent: true, opacity: 0, depthWrite: false, side: THREE.DoubleSide }));
      ring.rotation.x = -Math.PI / 2;
      ring.position.y = 0.03;
      const bubble = label('agent-bubble', `<div class="map3d-card"><b>${names[agent]}</b><span></span></div>`, [0.5, 1]);
      bubble.element.style.setProperty('--agent', colors[agent]);
      const group = new THREE.Group();
      group.add(sprite, shadow, ring, bubble);
      group.position.copy(posts[agent]);
      this.scene.add(group);
      return { agent, frames, mode: 'listening', sprite, material, shadow, ring, bubble, group, post: posts[agent], size: 0, active: false, text: '' };
    };
    this.mascots = Object.fromEntries(Object.keys(posts).map((agent) => [agent, make(agent)]));
    Object.values(this.mascots).forEach((mascot, index) => gsap.to(mascot, { size: 1, duration: reducedMotion ? 0 : 0.8, delay: reducedMotion ? 0 : 0.4 + index * 0.12, ease: 'back.out(2.2)' }));
  }

  // Live agent activity from the app: mode, who is on point, and what each one is saying.
  setAgents(statuses, lines) {
    if (!this.mascots) return;
    Object.entries(this.mascots).forEach(([id, mascot]) => {
      const status = statuses?.[id];
      if (!status) return;
      mascot.mode = { working: 'working', done: 'result' }[status.mode] || 'listening';
      mascot.idle = status.mode === 'idle';
      mascot.active = Boolean(status.active);
      const text = lines?.[id] || status.lines[0];
      const el = mascot.bubble.element;
      el.classList.toggle('active', mascot.active);
      el.classList.toggle('idle', mascot.idle);
      el.dataset.mode = status.mode;
      if (text !== mascot.text) {
        mascot.text = text;
        el.querySelector('b').textContent = `${{ observability: 'Observability', ghost: 'Ghost Test', blast: 'Blast Radius', resolution: 'Resolution' }[id]} · ${status.headline}`;
        el.querySelector('span').textContent = text;
        el.classList.remove('pop');
        void el.offsetWidth;
        el.classList.add('pop');
      }
    });
  }

  // Every 3D label the declutter pass may move, with how much it matters when space runs out.
  buildDeclutter() {
    const state = () => this.state || this.fixture.mapStates[0];
    const item = (el, kind, priority, active = () => true) => ({ el, kind, priority, active, offset: { x: 0, y: 0 }, target: { x: 0, y: 0 }, hidden: false });
    const nodePriority = { critical: 80, proposed: 75, suspect: 70, patching: 65, watch: 55, cleared: 50, healthy: 45 };
    this.declutterItems = [
      item(this.bugMarker.element, 'marker', 100, () => state().bug !== 'hidden' && !state().twin),
      item(this.lock.element, 'marker', 98, () => state().lock !== 'hidden'),
      item(this.crowdLabel.element, 'marker', 95),
      ...Object.values(this.mascots).map((mascot) => ({ ...item(mascot.bubble.element, 'marker', 0), priorityOf: () => (mascot.active ? 99 : 94) })),
      ...this.twinLabels.map((twinLabel) => item(twinLabel.element, 'marker', 92, () => state().twin)),
      ...[...this.nodes.values()].map((node) => ({ ...item(node.el, 'node', 0, () => !node.offscreen && !state().twin), node })),
      item(this.release.element, 'badge', 40, () => state().release && !state().twin),
      ...this.bubbles.map((bubble, index) => item(bubble.element, 'badge', 30 - index, () => index < state().bubbles)),
      ...this.laneLabels.map((el) => item(el, 'lane', 10)),
    ];
    this.declutterItems.filter((entry) => entry.node).forEach((entry) => {
      entry.priorityOf = () => nodePriority[entry.node.status] + (entry.node.id === this.selected || entry.node.id === this.hovered ? 30 : 0);
    });
    this.declutterFrame = 0;
  }

  // Resolve overlapping labels in screen space: important labels claim space first,
  // node labels rise on their stems, and minor labels step aside or fade out.
  declutter() {
    const items = this.declutterItems;
    if (this.declutterFrame++ % 3 === 0) {
      const placed = [];
      const pad = 5;
      const frame = this.host.getBoundingClientRect();
      const outside = (box) => Math.max(0, frame.left + 4 - box.left) + Math.max(0, box.right - frame.right + 4) + Math.max(0, frame.top + 4 - box.top) + Math.max(0, box.bottom - frame.bottom + 4);
      const hits = (box) => placed.reduce((sum, other) => {
        const w = Math.min(box.right, other.right) - Math.max(box.left, other.left) + pad;
        const h = Math.min(box.bottom, other.bottom) - Math.max(box.top, other.top) + pad;
        return w > 0 && h > 0 ? sum + w * h : sum;
      }, 0);
      const live = items
        .map((entry) => ({ entry, priority: entry.priorityOf ? entry.priorityOf() : entry.priority }))
        .filter(({ entry }) => entry.active())
        .sort((a, b) => b.priority - a.priority);
      items.forEach((entry) => { if (!entry.active()) entry.target = { x: 0, y: 0 }; });
      live.forEach(({ entry }) => {
        const rect = entry.el.getBoundingClientRect();
        if (!rect.width || !rect.height) return;
        const base = { left: rect.left - entry.offset.x, top: rect.top - entry.offset.y, width: rect.width, height: rect.height };
        const candidates = [entry.target, { x: 0, y: 0 }];
        const steps = entry.kind === 'node' ? [-12, -24, -36, -48, -62, -78, -96, -116, -140, 16, 32, 48] : [-14, 14, -28, 28, -44, 44, -62, 62, -84, 84];
        const sideways = entry.kind === 'node' ? [0, -40, 40, -80, 80] : [0, -60, 60];
        sideways.forEach((x) => steps.forEach((y) => candidates.push({ x, y })));
        if (entry.kind === 'node') sideways.slice(1).forEach((x) => candidates.splice(2, 0, { x, y: 0 }));
        let best = null;
        let bestScore = Infinity;
        for (const candidate of candidates) {
          const box = { left: base.left + candidate.x, top: base.top + candidate.y, right: base.left + candidate.x + base.width, bottom: base.top + candidate.y + base.height };
          const score = hits(box) + outside(box) * 400;
          if (score === 0) { best = { candidate, box }; bestScore = 0; break; }
          if (score < bestScore) { bestScore = score; best = { candidate, box }; }
        }
        const minor = entry.kind !== 'node' && entry.kind !== 'marker';
        entry.hidden = minor && bestScore > 0;
        entry.target = entry.hidden ? { x: 0, y: 0 } : best.candidate;
        if (!entry.hidden) placed.push(best.box);
      });
    }
    const ease = reducedMotion ? 1 : 0.22;
    items.forEach((entry) => {
      entry.offset.x += (entry.target.x - entry.offset.x) * ease;
      entry.offset.y += (entry.target.y - entry.offset.y) * ease;
      if (Math.abs(entry.offset.x) < 0.3 && Math.abs(entry.offset.y) < 0.3 && !entry.target.x && !entry.target.y) entry.offset = { x: 0, y: 0 };
      entry.el.style.translate = entry.offset.x || entry.offset.y ? `${entry.offset.x.toFixed(1)}px ${entry.offset.y.toFixed(1)}px` : '';
      if (entry.kind === 'node') {
        entry.el.style.setProperty('--lift', `${Math.max(0, -entry.offset.y).toFixed(1)}px`);
        entry.el.style.setProperty('--shift', `${entry.offset.x.toFixed(1)}px`);
      }
      entry.el.classList.toggle('declutter-hidden', entry.hidden);
    });
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
      const id = this.hovered && this.hovered !== this.selected ? this.hovered : null;
      if (id) this.zoomTo(id);
      else this.resetView();
    });
  }

  mount(container, { mode = 'full', intro = false } = {}) {
    const modeChanged = mode !== this.mode;
    this.mode = mode;
    container.appendChild(this.host);
    this.host.dataset.mode = mode;
    this.controls.enableZoom = mode === 'full';
    this.controls.enabled = !(mode === 'compact' && window.matchMedia('(pointer: coarse)').matches);
    this.renderer.domElement.style.touchAction = this.controls.enabled ? 'none' : 'pan-y';
    if (modeChanged) this.width = 0;
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

  setInsets(insets) {
    const next = { top: 0, right: 0, bottom: 0, left: 0, ...insets };
    if (Object.keys(next).every((key) => Math.abs(next[key] - this.insets[key]) < 2)) return;
    this.insets = next;
    this.width = 0;
    this.resize();
  }

  // Shift the projection so the scene centres in the area the overlays leave free.
  applyViewOffset() {
    const { top, right, bottom, left } = this.mode === 'full' ? this.insets : { top: 0, right: 0, bottom: 0, left: 0 };
    if (!top && !right && !bottom && !left) {
      this.camera.clearViewOffset();
      return;
    }
    this.camera.setViewOffset(this.width, this.height, -(left - right) / 2, -(top - bottom) / 2, this.width, this.height);
  }

  updateFrustum() {
    const aspect = this.width / this.height;
    Object.assign(this.camera, { left: (-VIEW_HEIGHT * aspect) / 2, right: (VIEW_HEIGHT * aspect) / 2, top: VIEW_HEIGHT / 2, bottom: -VIEW_HEIGHT / 2 });
    this.camera.updateProjectionMatrix();
  }

  // Find the largest zoom at which every point sits inside the free area.
  fit(points, direction, target, margin) {
    const { top, right, bottom, left } = this.mode === 'full' ? this.insets : { top: 0, right: 0, bottom: 0, left: 0 };
    const frame = {
      cx: (left - right) / this.width,
      cy: (bottom - top) / this.height,
      rx: ((this.width - left - right) / this.width) * margin,
      ry: ((this.height - top - bottom) / this.height) * margin,
    };
    const probe = this.camera.clone();
    probe.position.copy(target).addScaledVector(direction, CAMERA_DISTANCE);
    probe.lookAt(target);
    probe.updateMatrixWorld();
    const corner = new THREE.Vector3();
    const fits = (zoom) => {
      probe.zoom = zoom;
      probe.updateProjectionMatrix();
      return points.every((point) => {
        corner.copy(point).project(probe);
        return Math.abs(corner.x - frame.cx) <= frame.rx && Math.abs(corner.y - frame.cy) <= frame.ry;
      });
    };
    let low = 0.05;
    let high = 12;
    for (let i = 0; i < 30; i += 1) {
      const mid = (low + high) / 2;
      if (fits(mid)) low = mid;
      else high = mid;
    }
    return { position: probe.position.clone(), target: target.clone(), zoom: low };
  }

  labelPoints(ids, lift = 3.2) {
    return ids.flatMap((id) => {
      const node = this.nodes.get(id);
      return [node.home.clone().setY(node.height + lift), node.home.clone()];
    });
  }

  // The one resting view: square to the board, looking straight down the lanes.
  homeView() {
    const compact = this.mode === 'compact';
    const { minX, maxX, minZ, maxZ } = this.bounds;
    const corners = [minX, maxX].flatMap((x) => [minZ, maxZ].flatMap((z) => [new THREE.Vector3(x, 0, z), new THREE.Vector3(x, -0.9, z)]));
    const labels = compact ? [] : [
      ...this.labelPoints([...this.nodes.keys()]),
      ...[...this.twinNodes.values()].map((node) => node.home.clone().setY(TWIN_LIFT + node.height + 2)),
    ];
    return this.fit([...corners, ...labels], VIEW_DIRECTION, new THREE.Vector3(this.boardCenter.x, 0, 0), compact ? 0.98 : 0.97);
  }

  setView(view) {
    this.focusTween?.kill();
    this.camera.position.copy(view.position);
    this.controls.target.copy(view.target);
    this.camera.zoom = view.zoom;
    this.camera.updateProjectionMatrix();
    this.camera.lookAt(view.target);
    this.controls.update();
  }

  resize() {
    const rect = this.host.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const changed = Math.abs(rect.width - (this.width || 0)) > 1 || Math.abs(rect.height - (this.height || 0)) > 1;
    this.width = rect.width;
    this.height = rect.height;
    this.host.classList.toggle('tight', rect.width < 760);
    this.renderer.setSize(rect.width, rect.height, false);
    this.labels.setSize(rect.width, rect.height);
    this.updateFrustum();
    this.applyViewOffset();
    if (changed && !this.userMoved) this.setView(this.homeView());
  }

  // Camera moves only ever start from the viewer: the Suspect and Reset buttons.
  fly(view, duration = 1.2) {
    this.focusTween?.kill();
    const time = reducedMotion ? 0.01 : duration;
    this.focusTween = gsap.timeline({ onUpdate: () => this.camera.updateProjectionMatrix() })
      .to(this.camera.position, { x: view.position.x, y: view.position.y, z: view.position.z, duration: time, ease: 'power3.inOut' }, 0)
      .to(this.controls.target, { x: view.target.x, y: view.target.y, z: view.target.z, duration: time, ease: 'power3.inOut' }, 0)
      .to(this.camera, { zoom: view.zoom, duration: time, ease: 'power3.inOut' }, 0);
  }

  playIntro() {
    this.clearSelection();
    this.userMoved = false;
    this.setView(this.homeView());
    if (reducedMotion) return;
    [...this.nodes.values()].sort((a, b) => a.home.x - b.home.x || a.home.z - b.home.z).forEach((node, index) => {
      node.introY = 4;
      node.group.scale.setScalar(0.5);
      gsap.to(node, { introY: 0, duration: 0.8, delay: 0.1 + index * 0.04, ease: 'power3.out' });
      gsap.to(node.group.scale, { x: 1, y: 1, z: 1, duration: 0.7, delay: 0.1 + index * 0.04, ease: 'back.out(1.6)' });
    });
    this.edges.forEach((edge) => {
      if (edge.kind !== undefined) return;
      edge.draw = 0;
      gsap.to(edge, { draw: 1, duration: 0.9, delay: 0.5 + (edge.curve.v0.x - this.bounds.minX) * 0.02, ease: 'power2.inOut' });
    });
    this.particleState.forEach((p) => { p.alive = false; p.wait = 1.2 + Math.random() * 2; });
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
    this.districtMat.color.set(t.district);
    this.crowdNeutral.set(t.crowd);
    this.edgeMat.color.set(t.edge);
  }

  // Live data replaces the recorded fixture's numbers, hypotheses and approvals.
  setData(data) {
    this.fixture = { ...this.fixture, hypotheses: data.hypotheses, impact: data.impact, approvals: data.approvals, mapStates: data.mapStates, incident: data.incident, times: data.times };
  }

  setStep(step, force = false) {
    if (step === this.step && !force) return;
    const state = this.fixture.mapStates[step];
    this.step = step;
    this.state = state;
    const duration = reducedMotion ? 0 : 0.9;

    this.nodes.forEach((node) => {
      const status = state.status[node.id] || 'healthy';
      const changed = status !== node.status;
      node.status = status;
      node.el.className = `map3d-label status-${status}${this.selected === node.id ? ' selected' : ''}${this.twinNodes.has(node.id) ? ' twinned' : ''}${node.offscreen ? ' offscreen' : ''}`;
      node.el.querySelector('small').textContent = state.metrics[node.id] || node.data.metric;
      const em = node.el.querySelector('em');
      em.textContent = state.tags[node.id] || '';
      em.hidden = !state.tags[node.id];
      this.renderHypothesis(node, state.hypotheses[node.id]);
      const color = new THREE.Color(STATUS_COLORS[status]);
      gsap.to(node.color, { r: color.r, g: color.g, b: color.b, duration, ease: 'power2.out' });
      gsap.to(node, { raise: status === 'critical' ? 0.55 : status === 'proposed' ? 0.35 : 0, duration: duration * 1.2, ease: 'back.out(2.2)' });
      const halo = node.halo.userData.material;
      gsap.to(halo, { opacity: status === 'suspect' || status === 'proposed' ? 0.9 : 0, duration });
      halo.color.set(status === 'proposed' ? STATUS_COLORS.proposed : STATUS_COLORS.suspect);
      if (changed && status !== 'healthy' && duration) {
        gsap.fromTo(node.lift.scale, { x: 1.18, y: 0.78, z: 1.18 }, { x: 1, y: 1, z: 1, duration: 0.9, ease: 'elastic.out(1, 0.4)' });
      }
    });

    this.edges.forEach((edge) => {
      if (edge.kind === 'twin') return;
      gsap.to(edge, { alert: edge.bug && state.bug === 'alert' ? 1 : 0, duration });
      if (edge.kind === 'reroute') {
        gsap.to(edge, { draw: state.reroute === 'hidden' ? 0 : 1, duration: duration * 1.8, ease: 'power3.inOut' });
        const color = new THREE.Color(state.reroute === 'active' ? STATUS_COLORS.healthy : STATUS_COLORS.proposed);
        gsap.to(edge.material.color, { r: color.r, g: color.g, b: color.b, duration });
        gsap.to(edge.material.emissive, { r: color.r, g: color.g, b: color.b, duration });
      }
    });
    gsap.to(this.edges.get('gb>bankx').material, { opacity: state.reroute === 'active' ? 0.3 : 1, duration });

    gsap.to(this, { blastLevel: state.blast ? 1 : 0, twinLevel: state.twin ? 1 : 0, duration: state.twin ? duration * 1.6 : duration, ease: 'power3.inOut' });
    this.twinLabels.forEach((item) => item.element.classList.toggle('visible', state.twin));
    this.host.classList.toggle('twin-mode', state.twin);
    this.crowdTarget = state.customers;
    this.renderCrowdLabel(state.customers);
    this.bubbles.forEach((bubble, index) => bubble.element.classList.toggle('visible', index < state.bubbles));
    this.renderBug(state.bug);
    this.renderLock(state.lock);
    this.release.element.classList.toggle('visible', state.release);
    this.release.element.querySelector('.map3d-card').innerHTML = `<b>App v4.2</b> released at ${this.fixture.times?.release || '10:42'}`;
    this.placeMascots(step);
  }

  renderHypothesis(node, hypothesis) {
    const el = node.el.querySelector('.map3d-hyp');
    if (!hypothesis) {
      el.hidden = true;
      return;
    }
    const data = this.fixture.hypotheses.find((item) => item.id === hypothesis.id) || {};
    const score = hypothesis.score ?? (hypothesis.state === 'testing' ? 55 : 0);
    const value = hypothesis.state === 'open' ? 'open' : hypothesis.state === 'testing' ? 'testing' : `${hypothesis.score}%`;
    el.hidden = false;
    el.className = `map3d-hyp hyp-${hypothesis.state}`;
    el.innerHTML = `<div><b>${hypothesis.id}</b><span>${hypothesis.short || data.short || ''}</span></div><div><i><u style="width:${score}%"></u><s></s></i><em>${hypothesis.state === 'proven' ? '✓ ' : hypothesis.state === 'ruled' ? '✕ ' : ''}${value}</em></div>`;
  }

  renderCrowdLabel(customers) {
    const { affected, complained } = this.fixture.impact;
    const tone = customers.notified ? 'notified' : customers.affected >= 1 ? 'found' : customers.affected > 0 ? 'failing' : 'calm';
    this.crowdLabel.element.className = `map3d-crowd tone-${tone}`;
    const counts = customers.affected >= 1 || customers.notified
      ? `<div class="crowd-counts"><span><b class="wine">${format.format(complained)}</b> complained</span><span><b>${format.format(affected - complained)}</b> never did</span></div>`
      : '';
    this.crowdLabel.element.innerHTML = `<div class="map3d-card"><small>${affected ? `${format.format(affected)} customers` : 'Customers'} sending to Bank X</small><strong>${customers.caption}</strong><span>${customers.detail}</span>${counts}</div>`;
  }

  renderBug(mode) {
    const el = this.bugMarker.element;
    el.className = `map3d-bug bug-${mode}`;
    const copy = {
      hidden: '',
      silent: `<div class="map3d-card"><b>Acks stopped · ${this.fixture.times?.failure || '10:42'}</b><span>No alert fired. Nobody knows yet.</span></div>`,
      alert: `<div class="map3d-card">${WARN_ICON}<div><b>ACK TIMEOUT</b><span>Gateway B → Bank X · debits with no credit</span></div></div>`,
      bypassed: `<div class="map3d-card"><b>Bypassed</b><span>Bank X now routes via Gateway C</span></div>`,
    }[mode];
    el.innerHTML = copy;
  }

  renderLock(mode) {
    const el = this.lock.element;
    el.className = `map3d-lock lock-${mode}`;
    const approval = this.fixture.approvals.find((item) => item.kind === 'Routing change') || { id: 'Reroute' };
    el.innerHTML = mode === 'hidden' ? '' : mode === 'pending'
      ? `<div class="map3d-card">${LOCK_ICON}<div><b>Awaiting engineer approval</b><span>${approval.id} · reroute to Gateway C</span></div></div>`
      : `<div class="map3d-card">${UNLOCK_ICON}<div><b>Approved and applied</b><span>${approval.id} · Ghost Tests rerun green</span></div></div>`;
  }

  placeMascots(step) {
    Object.entries(this.mascots).forEach(([id, mascot]) => {
      const ride = id === 'ghost' && step === 3;
      if (mascot.ride && !ride) gsap.to(mascot.group.position, { x: mascot.post.x, y: 0, z: mascot.post.z, duration: reducedMotion ? 0 : 1.2, ease: 'power3.inOut' });
      mascot.ride = ride;
    });
  }

  clearSelection() {
    this.selected = null;
    this.focusTween?.kill();
    this.nodes.forEach((node) => node.el.classList.remove('selected'));
  }

  // Selecting a component highlights it and opens its details; the camera stays put.
  select(id) {
    this.clearSelection();
    this.selected = id;
    this.nodes.forEach((node) => node.el.classList.toggle('selected', node.id === id));
  }

  // Zoom in on a component the viewer clicked, keeping the same straight angle.
  zoomTo(id) {
    const crowd = id === 'crowd';
    const node = this.nodes.get(id);
    if (!node && !crowd) return;
    this.select(id);
    this.onSelect?.(id);
    const target = crowd ? new THREE.Vector3(this.field.centerX, 0, 0) : node.home.clone();
    this.userMoved = true;
    this.fly({ position: target.clone().addScaledVector(VIEW_DIRECTION, CAMERA_DISTANCE), target, zoom: this.homeView().zoom * (crowd ? 1.9 : 2.2) });
  }

  focusSuspect() {
    const suspect = [...this.nodes.values()].find((node) => node.status === 'critical');
    if (suspect) this.zoomTo(suspect.id);
  }

  resetView() {
    this.select(null);
    this.onSelect?.(null);
    this.userMoved = false;
    this.fly(this.homeView());
  }

  activeFlows() {
    const state = this.state || this.fixture.mapStates[0];
    return this.fixture.flows.filter((flow) => (flow.probe ? state.probes && this.twinLevel > 0.95 : true));
  }

  spawn(p) {
    const flows = this.activeFlows();
    const total = flows.reduce((sum, flow) => sum + flow.weight, 0);
    let pick = Math.random() * total;
    const flow = flows.find((item) => (pick -= item.weight) < 0) || flows[0];
    p.flow = flow;
    p.path = this.state?.reroute === 'active' && flow.rerouted ? flow.rerouted : flow.path;
    p.seg = 0;
    p.t = 0;
    p.speed = (flow.probe ? 4.2 : 3.4) * (0.8 + Math.random() * 0.4) * (reducedMotion ? 0.4 : 1);
    p.alive = true;
    p.dying = 0;
  }

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

    if (this.focusTween?.isActive()) this.camera.lookAt(this.controls.target);
    else this.controls.update();

    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hits = this.raycaster.intersectObjects([...[...this.nodes.values()].flatMap((node) => node.pickables), this.district], false);
    const hovered = hits[0]?.object.userData.nodeId || null;
    if (hovered !== this.hovered) {
      this.hovered = hovered;
      this.renderer.domElement.style.cursor = hovered && this.mode === 'full' ? 'pointer' : '';
      this.nodes.forEach((node) => node.el.classList.toggle('hover', node.id === hovered));
    }

    this.updateNodes(dt, time);
    this.updateEdges(time);
    this.updateCrowd(dt, time);
    this.updateParticles(dt, time);
    this.updateEffects(dt, time);
    this.updateMascots(dt, time);

    this.renderer.render(this.scene, this.camera);
    this.labels.render(this.scene, this.camera);
    this.declutter();
  }

  updateNodes(dt, time) {
    this.nodes.forEach((node) => {
      this.tmpPos.copy(node.home).setY(node.height + 1).project(this.camera);
      const offscreen = Math.abs(this.tmpPos.x) > 0.96 || this.tmpPos.y > 0.98 || this.tmpPos.y < -1;
      if (offscreen !== node.offscreen) {
        node.offscreen = offscreen;
        node.el.classList.toggle('offscreen', offscreen);
      }
      node.hover += ((node.id === this.hovered || node.id === this.selected ? 1 : 0) - node.hover) * Math.min(1, dt * 10);
      const critical = node.status === 'critical';
      node.group.position.y = node.introY;
      node.lift.position.y = node.raise + node.hover * 0.3;
      node.capMat.color.copy(node.color);
      node.capMat.emissive.copy(node.color);
      node.capMat.emissiveIntensity = critical ? 0.45 + Math.sin(time * 4.2) * 0.25 : node.status === 'proposed' ? 0.4 + Math.sin(time * 2.4) * 0.15 : 0.1;
      node.beamMat.opacity += ((critical ? 0.12 : 0) - node.beamMat.opacity) * Math.min(1, dt * 4);
      node.pulses.forEach((ring, index) => {
        const phase = (time * 0.62 + index * 0.5) % 1;
        ring.scale.setScalar(0.55 + phase * 1.25);
        ring.material.opacity = critical ? (1 - phase) * 0.75 : 0;
      });
      node.halo.rotation.y = time * 0.35;
    });
  }

  updateEdges(time) {
    const t = THEMES[this.theme];
    this.edges.forEach((edge) => {
      if (edge.kind === 'twin') return;
      edge.mesh.geometry.setDrawRange(0, Math.floor((edge.total * edge.draw) / 6) * 6);
      if (edge.kind === 'reroute') {
        edge.mesh.visible = edge.draw > 0.002;
        edge.material.emissiveIntensity = this.state?.reroute === 'proposed' ? 0.35 + Math.sin(time * 3) * 0.3 : 0.25;
      } else {
        this.tmpColor.set(t.edge).lerp(COLORS.coral, edge.alert);
        edge.material.color.copy(this.tmpColor);
        edge.material.emissive.copy(COLORS.coral).multiplyScalar(edge.alert * (0.35 + Math.sin(time * 4) * 0.2));
      }
    });
    const level = this.twinLevel;
    this.twin.position.y = (1 - level) * -3.5;
    this.twin.visible = level > 0.01;
    this.twinMats.body.opacity = level * 0.5;
    this.twinTinted.forEach((material) => { material.opacity = level * 0.55; });
    this.twinMats.line.opacity = level * 0.85;
    this.twinMats.tube.opacity = level * 0.7;
    this.twinMats.drop.opacity = level * 0.5;
    this.twinMats.deck.opacity = level * 0.9;
    ['~router>~gb', '~gb>~bankx', '~router>~gc', '~gc>~bankz'].forEach((key) => { this.edges.get(key).draw = level; });
    const bugAlert = this.edges.get('gb>bankx').alert;
    this.breakRing.material.opacity = bugAlert * (0.65 + Math.sin(time * 5) * 0.3);
    this.breakRing.scale.setScalar(1 + Math.sin(time * 5) * 0.12);
  }

  updateCrowd(dt, time) {
    const target = this.crowdTarget;
    const { radius } = this.field;
    const blend = reducedMotion ? 1 : Math.min(1, dt * 2.2);
    this.front += (target.affected - this.front) * Math.min(1, dt * (reducedMotion ? 60 : 1.1));
    const notified = target.notified;
    const yAxis = new THREE.Vector3(0, 1, 0);
    this.people.forEach((person, index) => {
      const affected = person.threshold < this.front;
      const complaining = person.complainer && target.complainers && !notified;
      const goal = notified ? COLORS.teal : complaining ? COLORS.wine : affected ? COLORS.shirt : this.crowdNeutral;
      const screenGoal = notified ? COLORS.screenOk : complaining ? COLORS.screenWine : affected ? COLORS.screenFail : this.screenIdle;
      person.color.lerp(goal, blend);
      person.screen.lerp(screenGoal, blend);
      person.lift += ((complaining ? 0.22 + Math.abs(Math.sin(time * 4 + person.phase)) * 0.12 : 0) - person.lift) * blend;
      this.tmpPos.set(person.x, 0.16 + person.lift * this.field.figure, person.z);
      this.tmpQuat.setFromAxisAngle(yAxis, person.yaw);
      this.tmpScale.setScalar(complaining ? 1.25 : 1);
      this.tmpMatrix.compose(this.tmpPos, this.tmpQuat, this.tmpScale);
      this.crowd.setMatrixAt(index, this.tmpMatrix);
      this.heads.setMatrixAt(index, this.tmpMatrix);
      this.phones.setMatrixAt(index, this.tmpMatrix);
      this.crowd.setColorAt(index, person.color);
      this.phones.setColorAt(index, person.screen);
    });
    this.tmpQuat.identity();
    [this.crowd, this.phones].forEach((mesh) => {
      mesh.instanceMatrix.needsUpdate = true;
      mesh.instanceColor.needsUpdate = true;
    });
    this.heads.instanceMatrix.needsUpdate = true;

    const tone = notified ? COLORS.teal : COLORS.coral;
    const spread = Math.max(0.001, this.front * radius * 1.04 + (this.front > 0.01 ? 0.3 : 0));
    this.frontDisc.scale.setScalar(spread);
    this.frontEdge.scale.setScalar(spread);
    this.frontMat.color.copy(tone);
    this.frontEdgeMat.color.copy(tone);
    this.frontMat.opacity += (((this.front > 0.01 ? (notified ? 0.07 : 0.13) : 0)) - this.frontMat.opacity) * blend;
    this.frontEdgeMat.opacity += (((this.front > 0.01 && this.front < 0.98 && !notified) ? 0.75 + Math.sin(time * 5) * 0.2 : 0) - this.frontEdgeMat.opacity) * blend;
    this.blastRings.forEach((ring) => {
      ring.material.opacity += (this.blastLevel * 0.55 - ring.material.opacity) * blend;
    });

    const ringOpacity = target.complainers && !notified ? 0.5 : 0;
    this.complainerRings.forEach((ring, index) => {
      const phase = (time * 0.7 + index * 0.13) % 1;
      ring.material.opacity += ((ringOpacity * (1 - phase)) - ring.material.opacity) * 0.3;
      ring.scale.setScalar(1 + phase * 1.6);
    });
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
      const edge = this.edges.get(`${p.path[p.seg]}>${p.path[p.seg + 1]}`);
      if (!edge || edge.draw < 0.98) {
        p.alive = false;
        p.wait = 0.5;
        this.tmpMatrix.makeScale(0, 0, 0);
        this.particles.setMatrixAt(index, this.tmpMatrix);
        return;
      }
      const failingSeg = edge.bug && p.flow.failing && (edge.kind === 'twin' || state.failing);
      if (p.dying > 0) {
        p.dying -= dt * 2.6;
        if (p.dying <= 0) { p.alive = false; p.wait = Math.random() * 1.4; }
      } else {
        p.t += (p.speed * dt) / edge.length;
        if (failingSeg && p.t >= 0.88) {
          p.t = 0.88;
          p.dying = 1;
          this.spark(edge.curve.getPointAt(0.9), time);
        } else if (p.t >= 1) {
          p.seg += 1;
          p.t = 0;
          if (p.seg >= p.path.length - 1) { p.alive = false; p.wait = 0.3 + Math.random() * 2.6; }
        }
      }
      if (!p.alive) {
        this.tmpMatrix.makeScale(0, 0, 0);
        this.particles.setMatrixAt(index, this.tmpMatrix);
        return;
      }
      edge.curve.getPointAt(Math.min(1, p.t), this.tmpPos);
      this.tmpPos.y += 0.05;
      if (edge.kind === 'twin') this.tmpPos.y += this.twin.position.y;
      const scale = (p.flow.probe ? 1.1 : 1) * (p.dying > 0 ? p.dying : 1) * Math.min(1, p.t * 6 + (p.seg > 0 ? 1 : 0));
      this.tmpScale.setScalar(scale);
      this.tmpMatrix.compose(this.tmpPos, this.tmpQuat, this.tmpScale);
      this.particles.setMatrixAt(index, this.tmpMatrix);
      const base = p.flow.probe ? COLORS.violet : COLORS.teal;
      this.tmpColor.copy(base);
      if (failingSeg) this.tmpColor.lerp(COLORS.coral, Math.min(1, p.t * 1.5));
      this.particles.setColorAt(index, this.tmpColor);
    });
    this.particles.instanceMatrix.needsUpdate = true;
    this.particles.instanceColor.needsUpdate = true;
  }

  spark(position, time) {
    if (time - this.lastSpark < 0.15) return;
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
    const phase = (time * 0.36) % 1;
    this.blast.scale.setScalar(1 + phase * (this.field.radius + 1.5) * 2.2);
    this.blastMat.opacity = this.blastLevel * (1 - phase) * 0.6;
  }

  updateMascots(dt, time) {
    Object.values(this.mascots).forEach((mascot) => {
      const frames = mascot.frames[mascot.mode] || mascot.frames.listening;
      const period = mascot.mode === 'working' ? 0.42 : mascot.idle ? 1.4 : 0.8;
      const frame = frames[Math.floor(time / period) % frames.length];
      if (mascot.material.map !== frame) mascot.material.map = frame;
      const scale = mascot.size * (mascot.active ? 3.4 : 2.6);
      if (mascot.ride) {
        const route = [this.edges.get('~router>~gb'), this.edges.get('~gb>~bankx')];
        const loop = (time * 0.12) % 2;
        const point = route[Math.floor(loop)].curve.getPointAt(loop % 1);
        this.tmpPos.copy(point).setY(0);
        mascot.group.position.lerp(this.tmpPos, Math.min(1, dt * 6));
        mascot.sprite.position.y = point.y + this.twin.position.y + 0.12;
        mascot.shadow.position.y = TWIN_LIFT + this.twin.position.y - 0.02;
      } else {
        const hop = mascot.mode === 'result' ? Math.abs(Math.sin(time * 2.4)) * 0.3 : mascot.mode === 'working' ? Math.abs(Math.sin(time * 6)) * 0.08 : Math.sin(time * 1.8) * 0.06;
        mascot.sprite.position.y = 0.05 + hop;
        mascot.shadow.position.y = 0.02;
      }
      mascot.material.opacity = mascot.idle ? 0.75 : 1;
      mascot.sprite.scale.setScalar(Math.max(0.0001, scale));
      mascot.shadow.scale.setScalar(Math.max(0.0001, mascot.size * (mascot.active ? 1.3 : 1)));
      mascot.shadow.material.opacity = mascot.size * 0.55;
      mascot.ring.material.opacity = mascot.active && !mascot.ride ? 0.55 + Math.sin(time * 3) * 0.25 : 0;
      mascot.ring.scale.setScalar(1.1 + Math.sin(time * 3) * 0.08);
      mascot.bubble.position.y = mascot.sprite.position.y + scale * 0.98;
    });
  }
}
