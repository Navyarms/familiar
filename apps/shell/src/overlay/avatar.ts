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

  // Procedural motion state (no animation clips: everything is layered sines + scheduled events, eased).
  private lookTarget = new THREE.Object3D(); // VRM look-at target (eyes)
  private gazeGoal = new THREE.Vector3(0, 1.3, 3);
  private nextSaccadeAt = 0;
  private glance = { yaw: 0, pitch: 0, roll: 0 }; // current eased head glance
  private glanceGoal = { yaw: 0, pitch: 0, roll: 0 };
  private nextGlanceAt = 0;
  private blink = { nextAt: 1.5, startedAt: -1, double: false };
  private speakEnergy = 0; // slow follower of mouth amplitude, drives nods and hand gestures
  private stateMix = { listening: 0, thinking: 0, speaking: 0 }; // eased 0..1 per state

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
        if (vrm.lookAt) {
          this.scene.add(this.lookTarget);
          this.lookTarget.position.copy(this.gazeGoal);
          vrm.lookAt.target = this.lookTarget;
        }
        this.scene.add(vrm.scene);
        this.vrm = vrm;
        this.animateHumanoid(0, 0, true); // settle into the rest pose before the first frame
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

  /** Smooth pseudo-noise in [-1, 1]: three incommensurate sines, so nothing visibly repeats. */
  private static noise(t: number, seed: number): number {
    return (Math.sin(t * 0.61 + seed) + 0.5 * Math.sin(t * 1.37 + seed * 2.1) + 0.25 * Math.sin(t * 2.93 + seed * 0.7)) / 1.75;
  }

  /** Procedural body motion on the normalized humanoid: rest pose + breathing + weight shift + glances + state. */
  private animateHumanoid(t: number, dt: number, snap = false): void {
    if (!this.vrm) return;
    const h = this.vrm.humanoid;
    const bone = (n: Parameters<typeof h.getNormalizedBoneNode>[0]) => h.getNormalizedBoneNode(n);
    const ease = (cur: number, goal: number, rate: number) => (snap ? goal : cur + (goal - cur) * Math.min(1, dt * rate));
    const N = Avatar.noise;

    // State weights ease in and out so transitions never pop.
    for (const k of Object.keys(this.stateMix) as (keyof typeof this.stateMix)[])
      this.stateMix[k] = ease(this.stateMix[k], this.state === k ? 1 : 0, 3);
    const { listening: L, thinking: T, speaking: S } = this.stateMix;
    this.speakEnergy = ease(this.speakEnergy, this.smoothedAmp, 6);
    const E = this.speakEnergy * S;

    // Breathing (4.2 s cycle) and a slow weight shift between the feet (~9 s, noise-driven).
    const breath = 0.5 + 0.5 * Math.sin(t * 1.5);
    const shift = N(t * 0.35, 1.0);
    const hips = bone('hips');
    if (hips) {
      hips.position.x = shift * 0.018;
      hips.position.y = -Math.abs(shift) * 0.005;
      hips.rotation.z = -shift * 0.035;
      hips.rotation.y = shift * 0.04 + N(t * 0.2, 4.0) * 0.025;
      hips.rotation.x = S * 0.015; // tiny forward lean when speaking
    }
    const spine = bone('spine');
    if (spine) {
      spine.rotation.z = shift * 0.02;
      spine.rotation.x = -0.01 * breath + N(t * 0.3, 2.0) * 0.012 + L * 0.03;
      spine.rotation.y = N(t * 0.25, 3.0) * 0.02;
    }
    const chest = bone('chest') ?? bone('upperChest');
    if (chest) chest.rotation.x = -0.022 * breath - L * 0.02;

    // Shoulders rise a hair with each breath; arms hang relaxed with slow drift and talk-gestures.
    for (const side of ['left', 'right'] as const) {
      const sgn = side === 'left' ? -1 : 1; // normalized space: left arm lowers with negative z
      const seed = side === 'left' ? 10 : 20;
      const sh = bone(`${side}Shoulder`);
      if (sh) sh.rotation.z = sgn * (0.02 * breath);
      // Normalized rig = T-pose with the arm along ±X, facing +Z. Lowering the arm is a rotation about Z;
      // swinging it forward (and bending the elbow forward) is a rotation about Y: left needs -Y, right +Y.
      const up = bone(`${side}UpperArm`);
      if (up) {
        const gesture = E * (0.10 * Math.sin(t * 2.7 + seed) + 0.05) + T * (side === 'right' ? 0.10 : 0);
        up.rotation.z = sgn * (1.22 - 0.06 * breath - gesture * 0.5);
        up.rotation.y = sgn * (0.06 + N(t * 0.4, seed) * 0.03 + gesture * 0.8); // a touch forward, more when talking
        up.rotation.x = N(t * 0.35, seed + 3) * 0.02;
      }
      const lo = bone(`${side}LowerArm`);
      if (lo) {
        const bend = 0.32 + N(t * 0.45, seed + 1) * 0.05 + E * (0.30 + 0.2 * Math.sin(t * 2.7 + seed + 1.2)) + T * (side === 'right' ? 0.75 : 0.05);
        lo.rotation.y = sgn * bend; // elbow flexion: hand comes forward
        lo.rotation.z = sgn * 0.04; // slightly inward
        lo.rotation.x = 0;
      }
      const hand = bone(`${side}Hand`);
      if (hand) {
        hand.rotation.z = sgn * 0.08; // relaxed wrist, fingers toward the thigh
        hand.rotation.y = N(t * 0.5, seed + 2) * 0.05 + E * 0.15 * Math.sin(t * 2.7 + seed + 2);
        hand.rotation.x = 0;
      }
      // Fingers: slight natural curl toward the palm instead of splayed flat hands.
      for (const f of ['Index', 'Middle', 'Ring', 'Little'] as const)
        for (const seg of ['Proximal', 'Intermediate', 'Distal'] as const) {
          const b = bone(`${side}${f}${seg}` as any);
          if (b) b.rotation.z = sgn * (seg === 'Proximal' ? 0.28 : 0.22) * (f === 'Index' ? 0.6 : 1);
        }
    }

    // Head: slow sway + occasional glances (new goal every 2.5–6 s) + state posture + nods with the voice.
    if (t >= this.nextGlanceAt) {
      this.nextGlanceAt = t + 2.5 + Math.random() * 3.5;
      const r = Math.random();
      this.glanceGoal = r < 0.45 ? { yaw: 0, pitch: 0, roll: 0 }
        : { yaw: (Math.random() - 0.5) * 0.5, pitch: (Math.random() - 0.5) * 0.25, roll: (Math.random() - 0.5) * 0.12 };
    }
    for (const k of ['yaw', 'pitch', 'roll'] as const) this.glance[k] = ease(this.glance[k], this.glanceGoal[k], 1.6);
    const idleYaw = N(t * 0.5, 5.0) * 0.09, idlePitch = N(t * 0.6, 6.0) * 0.04, idleRoll = N(t * 0.4, 7.0) * 0.035;
    const nod = -E * (0.05 + 0.04 * Math.sin(t * 5.1)) + S * N(t * 1.3, 8.0) * 0.03;
    const head = bone('head');
    const neck = bone('neck');
    const yaw = idleYaw + this.glance.yaw * (1 - L * 0.8) + T * 0.16 + S * N(t * 0.9, 9.0) * 0.05;
    const pitch = idlePitch + this.glance.pitch * (1 - L * 0.8) + T * -0.10 + L * 0.06 + nod;
    const roll = idleRoll + this.glance.roll + L * 0.07 + T * 0.05;
    if (head) head.rotation.set(pitch * 0.7, yaw * 0.7, roll * 0.7);
    if (neck) neck.rotation.set(pitch * 0.3, yaw * 0.3, roll * 0.3);

    // Eyes: saccades toward a point near the viewer; locked on the viewer while listening, up-right while thinking.
    if (this.vrm.lookAt) {
      if (t >= this.nextSaccadeAt) {
        this.nextSaccadeAt = t + 0.8 + Math.random() * 2.5;
        this.gazeGoal.set((Math.random() - 0.5) * 1.2, 1.3 + (Math.random() - 0.5) * 0.5, 3);
      }
      const goal = new THREE.Vector3().copy(this.gazeGoal);
      goal.lerp(new THREE.Vector3(0, 1.3, 3), L);
      goal.lerp(new THREE.Vector3(1.2, 2.2, 3), T * 0.7);
      this.lookTarget.position.lerp(goal, snap ? 1 : Math.min(1, dt * 12));
    }
  }

  update(): void {
    const dt = Math.min(this.clock.getDelta(), 0.1);
    const t = this.clock.elapsedTime;
    const now = performance.now() / 1000;
    if (this.vrm) {
      this.animateHumanoid(t, dt);
      const em = this.vrm.expressionManager;
      if (em) {
        // Blinks: random 2–6 s apart, 0.16 s each, sometimes a double; more often while thinking.
        if (this.blink.startedAt < 0 && t >= this.blink.nextAt) {
          this.blink.startedAt = t;
        }
        let blinkValue = 0;
        if (this.blink.startedAt >= 0) {
          const ph = (t - this.blink.startedAt) / 0.16;
          if (ph >= 1) {
            this.blink.startedAt = -1;
            if (!this.blink.double && Math.random() < 0.15) { this.blink.double = true; this.blink.nextAt = t + 0.12; }
            else { this.blink.double = false; this.blink.nextAt = t + (this.state === 'thinking' ? 1.5 : 2.5) + Math.random() * 3.5; }
          } else blinkValue = Math.sin(ph * Math.PI);
        }
        em.setValue('blink', blinkValue);
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
