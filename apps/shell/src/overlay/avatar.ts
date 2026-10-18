/**
 * Avatar renderer. Loads the persona's VRM (0.x or 1.0) or falls back to the built-in "wisp" orb.
 * Drives: idle motion, blink, lip sync from (amp, viseme), expression tags with decay, state glow.
 */
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { VRM, VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';

export type ExpressionSpec = { vrm: string; weight: number; decay_s: number };
const VISEMES = ['aa', 'ih', 'ou', 'ee', 'oh'] as const;

function radialGlowTexture(): THREE.Texture {
  const c = document.createElement('canvas');
  c.width = c.height = 256;
  const g = c.getContext('2d')!;
  const grad = g.createRadialGradient(128, 128, 0, 128, 128, 128);
  grad.addColorStop(0, 'rgba(255,255,255,0.9)');
  grad.addColorStop(0.35, 'rgba(255,255,255,0.35)');
  grad.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = grad;
  g.fillRect(0, 0, 256, 256);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

export class Avatar {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  readonly camera: THREE.PerspectiveCamera;
  private vrm: VRM | null = null;
  private orb: THREE.Group | null = null;
  private clock = new THREE.Clock();
  private expressions: Record<string, ExpressionSpec> = {};
  private active: { vrm: string; weight: number; until: number; decay: number }[] = [];
  private smoothedAmp = 0;
  private shape: string = 'aa';
  state: 'idle' | 'thinking' | 'speaking' | 'listening' | 'booting' = 'booting';

  constructor(canvasParent: HTMLElement) {
    this.renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, premultipliedAlpha: true });
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.setPixelRatio(window.devicePixelRatio);
    this.renderer.setSize(window.innerWidth, window.innerHeight);
    canvasParent.appendChild(this.renderer.domElement);
    this.camera = new THREE.PerspectiveCamera(30, window.innerWidth / window.innerHeight, 0.1, 20);
    this.camera.position.set(0, 1.25, 3.1);
    this.camera.lookAt(0, 1.05, 0);
    const key = new THREE.DirectionalLight(0xffffff, Math.PI * 0.9);
    key.position.set(1, 1.5, 1.2);
    this.scene.add(key, new THREE.AmbientLight(0xffffff, 0.55));
    window.addEventListener('resize', () => {
      this.renderer.setSize(window.innerWidth, window.innerHeight);
      this.camera.aspect = window.innerWidth / window.innerHeight;
      this.camera.updateProjectionMatrix();
    });
  }

  setExpressionMap(map: Record<string, ExpressionSpec>): void {
    this.expressions = map;
  }

  async load(vrmPath: string | null): Promise<string> {
    this.clearModel();
    if (vrmPath) {
      try {
        const loader = new GLTFLoader();
        loader.register((p) => new VRMLoaderPlugin(p));
        // The main process serves the active persona's model; the path itself is only used as a cache-buster.
        const url = `familiar://avatar/current.vrm?v=${encodeURIComponent(vrmPath)}`;
        const gltf = await loader.loadAsync(url);
        const vrm = gltf.userData.vrm as VRM;
        VRMUtils.removeUnnecessaryVertices(gltf.scene);
        VRMUtils.combineSkeletons(gltf.scene);
        VRMUtils.rotateVRM0(vrm);
        vrm.scene.traverse((o) => (o.frustumCulled = false));
        const l = vrm.humanoid.getNormalizedBoneNode('leftUpperArm');
        const r = vrm.humanoid.getNormalizedBoneNode('rightUpperArm');
        if (l) l.rotation.z = -1.2;
        if (r) r.rotation.z = 1.2;
        this.scene.add(vrm.scene);
        this.vrm = vrm;
        return `vrm ${vrm.meta?.metaVersion ?? '0'}`;
      } catch (e) {
        console.error('VRM load failed, using orb', e);
      }
    }
    this.buildOrb();
    return 'orb';
  }

  private clearModel(): void {
    if (this.vrm) {
      this.scene.remove(this.vrm.scene);
      VRMUtils.deepDispose(this.vrm.scene);
      this.vrm = null;
    }
    if (this.orb) {
      this.scene.remove(this.orb);
      this.orb = null;
    }
  }

  /** Built-in original character: a soft glowing wisp with a halo ring; mouth = pulse. */
  private buildOrb(): void {
    const g = new THREE.Group();
    const core = new THREE.Mesh(new THREE.SphereGeometry(0.22, 48, 48),
      new THREE.MeshPhysicalMaterial({ color: 0x9fd3ff, emissive: 0x3d8bff, emissiveIntensity: 1.2, roughness: 0.2, transmission: 0.4, thickness: 0.5 }));
    const halo = new THREE.Mesh(new THREE.TorusGeometry(0.36, 0.015, 16, 96),
      new THREE.MeshBasicMaterial({ color: 0xbfe3ff, transparent: true, opacity: 0.7 }));
    halo.rotation.x = Math.PI / 2.4;
    const glow = new THREE.Sprite(new THREE.SpriteMaterial({ map: radialGlowTexture(), color: 0x6fb6ff, transparent: true, opacity: 0.6, depthWrite: false, blending: THREE.AdditiveBlending }));
    glow.scale.set(1.1, 1.1, 1);
    g.add(core, halo, glow);
    g.position.set(0, 1.05, 0);
    g.userData = { core, halo, glow };
    this.scene.add(g);
    this.orb = g;
  }

  setExpression(tag: string): void {
    const spec = this.expressions[tag];
    if (!spec) return;
    const now = performance.now() / 1000;
    this.active = this.active.filter((a) => a.vrm !== spec.vrm);
    this.active.push({ vrm: spec.vrm, weight: spec.weight, until: now + spec.decay_s, decay: spec.decay_s });
  }

  setMouth(amp: number, shape: string): void {
    this.smoothedAmp += (amp - this.smoothedAmp) * 0.55;
    this.shape = VISEMES.includes(shape as any) ? shape : this.shape;
  }

  update(): void {
    const dt = Math.min(this.clock.getDelta(), 0.1);
    const t = this.clock.elapsedTime;
    const now = performance.now() / 1000;
    if (this.vrm) {
      const h = this.vrm.humanoid;
      const head = h.getNormalizedBoneNode('head');
      const neck = h.getNormalizedBoneNode('neck');
      const spine = h.getNormalizedBoneNode('spine');
      const sway = this.state === 'thinking' ? 0.18 : 0.1;
      if (head) {
        head.rotation.y = Math.sin(t * 0.6) * sway + (this.state === 'thinking' ? 0.12 : 0);
        head.rotation.x = Math.sin(t * 1.1) * 0.04 + (this.state === 'thinking' ? 0.08 : 0);
        head.rotation.z = Math.sin(t * 0.4) * 0.03;
      }
      if (neck) neck.rotation.y = Math.sin(t * 0.6 + 0.3) * 0.04;
      if (spine) spine.position.y = Math.sin(t * 1.5) * 0.004; // breathing
      const em = this.vrm.expressionManager;
      if (em) {
        const blinkPhase = t % 4.3;
        em.setValue('blink', blinkPhase < 0.13 ? Math.sin((blinkPhase / 0.13) * Math.PI) : 0);
        for (const v of VISEMES) em.setValue(v, 0);
        if (this.smoothedAmp > 0.02) {
          em.setValue(this.shape, Math.min(1, this.smoothedAmp * 1.1));
          if (this.shape !== 'aa') em.setValue('aa', this.smoothedAmp * 0.35);
        }
        const names = new Set(em.expressions.map((e) => e.expressionName));
        for (const name of ['happy', 'sad', 'angry', 'surprised', 'relaxed', 'neutral', 'blinkLeft', 'blinkRight'])
          if (names.has(name) && name !== 'neutral') em.setValue(name, 0);
        for (const a of this.active) {
          const remaining = a.until - now;
          const w = remaining <= 0 ? 0 : a.weight * Math.min(1, remaining / Math.max(0.3, a.decay * 0.4));
          if (names.has(a.vrm)) em.setValue(a.vrm, Math.max(em.getValue(a.vrm) ?? 0, w));
        }
        this.active = this.active.filter((a) => a.until > now);
      }
      this.vrm.update(dt);
    } else if (this.orb) {
      const { core, halo, glow } = this.orb.userData as { core: THREE.Mesh; halo: THREE.Mesh; glow: THREE.Sprite };
      const bob = Math.sin(t * 1.3) * 0.04;
      this.orb.position.y = 1.05 + bob;
      const pulse = 1 + this.smoothedAmp * 0.35 + (this.state === 'thinking' ? Math.sin(t * 6) * 0.05 : 0);
      core.scale.setScalar(pulse);
      halo.rotation.z = t * (this.state === 'thinking' ? 2.2 : 0.5);
      halo.rotation.x = Math.PI / 2.4 + Math.sin(t * 0.7) * 0.2;
      const mat = core.material as THREE.MeshPhysicalMaterial;
      const target = this.state === 'listening' ? new THREE.Color(0x7dffb3) : this.state === 'thinking' ? new THREE.Color(0xffd27d) : new THREE.Color(0x3d8bff);
      mat.emissive.lerp(target, 0.05);
      glow.material.opacity = 0.25 + this.smoothedAmp * 0.4;
    }
    this.renderer.render(this.scene, this.camera);
  }

  /** Alpha of the pixel under (x, y) in CSS px, for click-through. */
  alphaAt(x: number, y: number): number {
    const gl = this.renderer.getContext();
    const px = new Uint8Array(4);
    gl.readPixels(Math.floor(x * window.devicePixelRatio), Math.floor((window.innerHeight - y) * window.devicePixelRatio), 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
    return px[3];
  }
}
