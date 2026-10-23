import React, { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import {
  OrbitControls,
  Environment,
  ContactShadows,
  Grid,
  Html,
  useProgress,
} from "@react-three/drei";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { PLYLoader } from "three/examples/jsm/loaders/PLYLoader.js";

interface Viewer3DProps {
  src: string;
  format: "glb" | "ply" | "demo";
  showGrid?: boolean;
  autoRotate?: boolean;
  background?: string;
}

function LoaderScreen() {
  const { progress, active } = useProgress();
  return (
    <Html center>
      <div className="viewer-loader">
        <div className="viewer-spinner" />
        <div className="viewer-loader-text">
          {active ? `Loading ${progress.toFixed(0)}%` : "Preparing..."}
        </div>
      </div>
    </Html>
  );
}

interface ModelProps {
  src: string;
  format: "glb" | "ply";
  onBoundingBox?: (box: THREE.Box3) => void;
}

function Model({ src, format, onBoundingBox }: ModelProps) {
  const groupRef = useRef<THREE.Group>(null);
  const meshRef = useRef<THREE.Mesh | null>(null);

  useEffect(() => {
    let disposed = false;
    const manager = new THREE.LoadingManager();
    const group = groupRef.current;
    if (!group) return;

    while (group.children.length > 0) {
      group.remove(group.children[0]);
    }

    const onLoaded = (object: THREE.Object3D | THREE.BufferGeometry) => {
      if (disposed) return;

      let mesh: THREE.Mesh;
      if (object instanceof THREE.BufferGeometry) {
        object.computeBoundingBox();
        if (!object.attributes.normal || object.attributes.normal.count === 0) {
          object.computeVertexNormals();
        }
        object.computeBoundingSphere();

        const hasColor =
          object.attributes.color !== undefined &&
          object.attributes.color.count > 0;
        const material = new THREE.MeshStandardMaterial({
          vertexColors: hasColor,
          color: hasColor ? undefined : 0xcccccc,
          side: THREE.FrontSide,
          flatShading: false,
          roughness: 0.92,
          metalness: 0.02,
          polygonOffset: true,
          polygonOffsetFactor: 2,
          polygonOffsetUnits: 2,
        });
        mesh = new THREE.Mesh(object, material);
      } else {
        mesh = object as unknown as THREE.Mesh;
      }

      mesh.castShadow = format !== "ply";
      mesh.receiveShadow = false;
      mesh.renderOrder = format === "ply" ? 10 : 0;
      group.add(mesh);
      meshRef.current = mesh;

      let box = new THREE.Box3().setFromObject(group);
      if (box.min.y < 0.02) {
        group.position.y += 0.02 - box.min.y;
        box = new THREE.Box3().setFromObject(group);
      }
      if (onBoundingBox) onBoundingBox(box);
    };

    if (format === "glb") {
      const loader = new GLTFLoader(manager);
      loader.load(
        src,
        (gltf) => onLoaded(gltf.scene as unknown as THREE.Mesh),
        undefined,
        (err) => console.error("GLB load error:", err)
      );
    } else {
      const loader = new PLYLoader(manager);
      loader.load(
        src,
        (geometry) => onLoaded(geometry),
        undefined,
        (err) => console.error("PLY load error:", err)
      );
    }

    return () => {
      disposed = true;
    };
  }, [src, format, onBoundingBox]);

  return <group ref={groupRef} />;
}

function DemoExcavationScene({
  onBoundingBox,
}: {
  onBoundingBox?: (box: THREE.Box3) => void;
}) {
  const groupRef = useRef<THREE.Group>(null);

  useEffect(() => {
    const group = groupRef.current;
    if (!group || !onBoundingBox) return;
    const box = new THREE.Box3().setFromObject(group);
    onBoundingBox(box);
  }, [onBoundingBox]);

  const artifactMaterial = new THREE.MeshStandardMaterial({
    color: "#1a1c22",
    roughness: 0.95,
    metalness: 0.03,
  });
  const markerMaterial = new THREE.MeshStandardMaterial({
    color: "#7c8796",
    roughness: 0.9,
    metalness: 0.02,
  });

  return (
    <group ref={groupRef}>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.28, 0]} receiveShadow>
        <planeGeometry args={[8.4, 12]} />
        <meshStandardMaterial color="#f8fafc" roughness={0.95} metalness={0} />
      </mesh>

      <mesh position={[0.1, 0.25, 2.7]} rotation={[0.08, -0.18, 0.02]} castShadow receiveShadow>
        <boxGeometry args={[2.6, 2.1, 2.2]} />
        <primitive object={artifactMaterial.clone()} attach="material" />
      </mesh>
      <mesh position={[-1.0, 0.15, 0.3]} rotation={[0.06, 0.22, -0.05]} castShadow receiveShadow>
        <boxGeometry args={[2.4, 1.7, 2.0]} />
        <primitive object={artifactMaterial.clone()} attach="material" />
      </mesh>
      <mesh position={[1.2, 0.4, -2.8]} rotation={[0.12, -0.1, 0.04]} castShadow receiveShadow>
        <boxGeometry args={[3.2, 2.8, 2.5]} />
        <primitive object={artifactMaterial.clone()} attach="material" />
      </mesh>

      {[
        [-2.9, -0.1, 4.2],
        [2.7, -0.1, -4.4],
        [-2.6, -0.1, -4.0],
        [2.3, -0.1, 4.0],
        [0.4, -0.1, -4.2],
        [1.6, -0.1, -4.0],
      ].map((pos, idx) => (
        <mesh key={idx} position={pos as [number, number, number]} castShadow receiveShadow>
          <dodecahedronGeometry args={[0.34, 0]} />
          <primitive object={markerMaterial.clone()} attach="material" />
        </mesh>
      ))}

      <mesh position={[-1.9, -0.21, -4.45]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <boxGeometry args={[0.95, 0.02, 0.18]} />
        <meshStandardMaterial color="#f8b4c0" roughness={0.8} />
      </mesh>
    </group>
  );
}

function HorseHeadScene({
  onBoundingBox,
}: {
  onBoundingBox?: (box: THREE.Box3) => void;
}) {
  const groupRef = useRef<THREE.Group>(null);
  const geometry = useMemo(() => {
    const shape = new THREE.Shape();
    const points: [number, number][] = [
      [-3.10, -0.35],
      [-2.85, 0.15],
      [-2.20, 0.55],
      [-1.45, 0.85],
      [-1.10, 1.28],
      [-0.98, 1.62],
      [-0.78, 1.18],
      [-0.48, 1.02],
      [-0.12, 1.52],
      [0.02, 2.18],
      [0.24, 1.48],
      [0.60, 1.06],
      [0.92, 1.00],
      [1.18, 1.46],
      [1.38, 1.98],
      [1.62, 1.24],
      [1.92, 0.70],
      [2.48, 0.88],
      [2.76, 0.52],
      [2.22, 0.18],
      [1.68, -0.10],
      [1.30, -0.92],
      [1.66, -2.12],
      [1.08, -3.00],
      [0.42, -3.20],
      [0.12, -2.52],
      [-0.34, -2.20],
      [-0.88, -2.72],
      [-1.64, -2.36],
      [-2.20, -1.82],
      [-2.72, -1.10],
      [-3.06, -0.72],
    ];

    shape.moveTo(points[0][0], points[0][1]);
    for (const [x, y] of points.slice(1)) {
      shape.lineTo(x, y);
    }
    shape.closePath();

    const eye = new THREE.Path();
    const eyePoints: [number, number][] = [
      [0.88, -0.22],
      [1.22, -0.78],
      [0.92, -1.38],
      [0.48, -0.88],
    ];
    eye.moveTo(eyePoints[0][0], eyePoints[0][1]);
    for (const [x, y] of eyePoints.slice(1)) {
      eye.lineTo(x, y);
    }
    eye.closePath();
    shape.holes.push(eye);

    const geo = new THREE.ExtrudeGeometry(shape, {
      depth: 0.22,
      bevelEnabled: false,
      steps: 1,
    });
    geo.computeVertexNormals();
    geo.computeBoundingBox();

    const box = geo.boundingBox;
    if (box) {
      const centerX = (box.min.x + box.max.x) / 2;
      const centerY = (box.min.y + box.max.y) / 2;
      geo.translate(-centerX, -centerY, 0);
    }
    return geo;
  }, []);

  useEffect(() => {
    const group = groupRef.current;
    if (!group || !onBoundingBox) return;
    const box = new THREE.Box3().setFromObject(group);
    onBoundingBox(box);
  }, [onBoundingBox]);

  return (
    <group ref={groupRef} rotation={[-Math.PI / 2, 0, -0.36]} position={[0, 0.001, 0]}>
      <mesh geometry={geometry} castShadow={false} receiveShadow={false}>
        <meshStandardMaterial
          color="#c58a1e"
          roughness={0.86}
          metalness={0.18}
          flatShading
        />
      </mesh>
    </group>
  );
}

function CameraRig({
  targetBox,
  controlsRef,
}: {
  targetBox: THREE.Box3 | null;
  controlsRef: React.RefObject<any>;
}) {
  const { camera } = useThree();
  const perspectiveCamera = camera as THREE.PerspectiveCamera;
  const fittedRef = useRef<string>("");

  useEffect(() => {
    if (!targetBox || targetBox.isEmpty()) return;

    const size = new THREE.Vector3();
    targetBox.getSize(size);
    const center = new THREE.Vector3();
    targetBox.getCenter(center);

    const signature = [
      center.x.toFixed(3),
      center.y.toFixed(3),
      center.z.toFixed(3),
      size.x.toFixed(3),
      size.y.toFixed(3),
      size.z.toFixed(3),
    ].join("|");
    if (fittedRef.current === signature) {
      return;
    }
    fittedRef.current = signature;

    const maxDim = Math.max(size.x, size.y, size.z);
    const fov = (perspectiveCamera.fov * Math.PI) / 180;
    const distance = Math.abs(maxDim / 2 / Math.tan(fov / 2)) * 1.6;

    perspectiveCamera.position.set(
      center.x + distance * 0.7,
      center.y + distance * 0.5,
      center.z + distance * 0.7
    );
    perspectiveCamera.near = Math.max(0.01, maxDim / 1000);
    perspectiveCamera.far = Math.max(1000, distance * 20);
    perspectiveCamera.updateProjectionMatrix();

    const controls = controlsRef.current;
    if (controls?.target) {
      controls.target.copy(center);
      controls.minDistance = Math.max(0.1, maxDim * 0.2);
      controls.maxDistance = Math.max(25, distance * 6);
      controls.update();
    }
  }, [targetBox, perspectiveCamera, controlsRef]);

  return null;
}

export const Viewer3D: React.FC<Viewer3DProps> = ({
  src,
  format,
  showGrid = true,
  autoRotate = false,
  background = "#0f172a",
}) => {
  const [bbox, setBbox] = useState<THREE.Box3 | null>(null);
  const [wireframe, setWireframe] = useState(false);
  const [showAxes, setShowAxes] = useState(false);
  const controlsRef = useRef<any>(null);
  const sceneRef = useRef<THREE.Group>(null);
  const isPlyView = format === "ply";
  const bboxSigRef = useRef("");

  const handleBbox = useCallback((box: THREE.Box3) => {
    if (box.isEmpty()) return;

    const size = new THREE.Vector3();
    const center = new THREE.Vector3();
    box.getSize(size);
    box.getCenter(center);

    const sig = [
      center.x.toFixed(4),
      center.y.toFixed(4),
      center.z.toFixed(4),
      size.x.toFixed(4),
      size.y.toFixed(4),
      size.z.toFixed(4),
    ].join("|");

    if (bboxSigRef.current === sig) {
      return;
    }
    bboxSigRef.current = sig;
    setBbox(box.clone());
  }, []);

  useEffect(() => {
    const root = sceneRef.current;
    if (!root) return;

    root.traverse((obj) => {
      const mesh = obj as THREE.Mesh;
      if (!mesh.isMesh || !mesh.material) return;

      const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      for (const material of materials) {
        if ("wireframe" in material) {
          material.wireframe = wireframe;
          material.needsUpdate = true;
        }
      }
    });
  }, [wireframe, src, format]);

  return (
    <div className="viewer-container">
      <div className="viewer-toolbar">
        <button
          className={`tool-btn ${wireframe ? "active" : ""}`}
          onClick={() => setWireframe((w) => !w)}
          title="Toggle Wireframe"
        >
          Wireframe
        </button>
        <button
          className={`tool-btn ${showAxes ? "active" : ""}`}
          onClick={() => setShowAxes((a) => !a)}
          title="Show Axes Helper"
        >
          Axes
        </button>
        <span className="tool-divider" />
        <span className="tool-label">Format: {format.toUpperCase()}</span>
        {bbox && !bbox.isEmpty() && (
          <span className="tool-label right">
            Size: {bbox.max.x - bbox.min.x > 0
              ? `${(bbox.max.x - bbox.min.x).toFixed(2)} × ${(bbox.max.y - bbox.min.y).toFixed(2)} × ${(bbox.max.z - bbox.min.z).toFixed(2)}`
              : "—"}
          </span>
        )}
      </div>

      <Canvas
        shadows={!isPlyView}
        camera={{ position: [4, 3, 4], fov: 45 }}
        gl={{ antialias: true, alpha: false, logarithmicDepthBuffer: true }}
        style={{ background, touchAction: "none", cursor: "grab" }}
      >
        <color attach="background" args={[background]} />

        <ambientLight intensity={isPlyView ? 0.9 : 0.6} />
        <directionalLight
          position={[5, 8, 5]}
          intensity={isPlyView ? 0.75 : 1.2}
          castShadow={!isPlyView}
          shadow-mapSize-width={2048}
          shadow-mapSize-height={2048}
        />
        <directionalLight position={[-5, 3, -5]} intensity={0.4} color="#a0c4ff" />

        <Suspense fallback={<LoaderScreen />}>
          <group ref={sceneRef}>
            {src === "demo:horse-head" ? (
              <HorseHeadScene onBoundingBox={handleBbox} />
            ) : format === "demo" ? (
              <DemoExcavationScene onBoundingBox={handleBbox} />
            ) : (
              <Model src={src} format={format} onBoundingBox={handleBbox} />
            )}
          </group>

          <Environment preset="city" />
          {!isPlyView && (
            <ContactShadows
              position={[0, -0.01, 0]}
              opacity={0.5}
              scale={10}
              blur={2.5}
              far={4}
            />
          )}
          {showGrid && (
            <Grid
              position={[0, -0.01, 0]}
              args={[20, 20]}
              cellSize={0.5}
              cellThickness={0.6}
              cellColor="#334155"
              sectionSize={2.5}
              sectionThickness={1.2}
              sectionColor="#64748b"
              fadeDistance={25}
              fadeStrength={1}
              infiniteGrid
            />
          )}
          {showAxes && <axesHelper args={[1.5]} />}
          <CameraRig targetBox={bbox} controlsRef={controlsRef} />
        </Suspense>

        <OrbitControls
          ref={controlsRef}
          makeDefault
          enableRotate
          enableZoom
          enablePan
          enableDamping
          dampingFactor={0.05}
          autoRotate={autoRotate}
          autoRotateSpeed={0.8}
          minDistance={0.1}
          maxDistance={100}
        />
      </Canvas>
    </div>
  );
};
