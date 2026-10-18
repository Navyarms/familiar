import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';

const hud = document.getElementById('hud');
const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, premultipliedAlpha: true });
renderer.setClearColor(0x000000, 0); // fully transparent background
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setSize(window.innerWidth, window.innerHeight);
document.body.appendChild(renderer.domElement);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(30, window.innerWidth / window.innerHeight, 0.1, 20);
camera.position.set(0, 1.2, 3.2);
camera.lookAt(0, 1.0, 0);
scene.add(new THREE.DirectionalLight(0xffffff, Math.PI).translateX(1).translateY(1).translateZ(1));
scene.add(new THREE.AmbientLight(0xffffff, 0.6));

let vrm = null;
const loader = new GLTFLoader();
loader.register((parser) => new VRMLoaderPlugin(parser));
const t0 = performance.now();
loader.load('../_downloads/vrm/Seed-san.vrm', (gltf) => {
  vrm = gltf.userData.vrm;
  VRMUtils.removeUnnecessaryVertices(gltf.scene);
  VRMUtils.combineSkeletons(gltf.scene);
  VRMUtils.rotateVRM0(vrm); // VRM 0.x faces +Z; make it face the camera
  vrm.scene.traverse((o) => { o.frustumCulled = false; });
  scene.add(vrm.scene);
  // Relax the arms from the T-pose so it looks idle
  const h = vrm.humanoid;
  const l = h.getNormalizedBoneNode('leftUpperArm'), r = h.getNormalizedBoneNode('rightUpperArm');
  if (l) l.rotation.z = -1.2; if (r) r.rotation.z = 1.2;
  console.log(`VRM loaded in ${(performance.now() - t0).toFixed(0)} ms, meta version ${vrm.meta?.metaVersion}, expressions:`,
    vrm.expressionManager ? vrm.expressionManager.expressions.map((e) => e.expressionName).join(',') : 'none');
}, undefined, (e) => { hud.textContent = 'load error: ' + e; console.error(e); });

// --- hit testing: main process sends cursor positions; we read back the alpha of that pixel.
const gl = renderer.getContext();
const px = new Uint8Array(4);
let cursor = null, overAvatar = false, lastSent = null;
window.overlay.onCursor((p) => { cursor = p; });
function hitTest() {
  if (!cursor) return;
  const x = Math.floor(cursor.x * window.devicePixelRatio);
  const y = Math.floor((window.innerHeight - cursor.y) * window.devicePixelRatio);
  gl.readPixels(x, y, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
  overAvatar = px[3] > 10;
  if (overAvatar !== lastSent) { lastSent = overAvatar; window.overlay.hit(overAvatar); }
}

// drag to reposition when over the avatar (we only get these events when not click-through)
let dragging = false, last = null;
window.addEventListener('mousedown', (e) => { if (overAvatar) { dragging = true; last = { x: e.screenX, y: e.screenY }; } });
window.addEventListener('mouseup', () => { dragging = false; });
window.addEventListener('mousemove', (e) => {
  if (dragging) { window.overlay.drag(e.screenX - last.x, e.screenY - last.y); last = { x: e.screenX, y: e.screenY }; }
});
window.addEventListener('keydown', (e) => { if (e.key === 'Escape') window.overlay.quit(); });

// --- render loop with FPS measurement -----------------------------------------------
const clock = new THREE.Clock();
let frames = 0, tAcc = 0, fps = 0;
const fpsLog = [];
function animate() {
  requestAnimationFrame(animate);
  const dt = clock.getDelta();
  const t = clock.elapsedTime;
  if (vrm) {
    // idle: breathing + head sway + blink + a fake lip-sync cycle to exercise expressions
    const head = vrm.humanoid.getNormalizedBoneNode('head');
    if (head) { head.rotation.y = Math.sin(t * 0.7) * 0.12; head.rotation.x = Math.sin(t * 1.1) * 0.04; }
    const chest = vrm.humanoid.getNormalizedBoneNode('chest') || vrm.humanoid.getNormalizedBoneNode('spine');
    if (chest) chest.position.y = Math.sin(t * 1.6) * 0.005;
    const em = vrm.expressionManager;
    if (em) {
      em.setValue('blink', (t % 3.5) < 0.12 ? 1 : 0);
      em.setValue('aa', Math.max(0, Math.sin(t * 9)) * 0.7);
      em.setValue('happy', 0.3);
    }
    vrm.update(dt);
  }
  renderer.render(scene, camera);
  hitTest();
  frames++; tAcc += dt;
  if (tAcc >= 1) {
    fps = frames / tAcc; frames = 0; tAcc = 0;
    fpsLog.push(fps);
    window.overlay.fps(fps.toFixed(1));
    const avg = fpsLog.reduce((a, b) => a + b, 0) / fpsLog.length;
    hud.textContent = `fps ${fps.toFixed(1)} (avg ${avg.toFixed(1)} over ${fpsLog.length}s)\nover avatar: ${overAvatar}  click-through: ${!overAvatar}\nEsc quits · drag avatar to move`;
  }
}
animate();
