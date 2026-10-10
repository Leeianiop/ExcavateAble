import React, { Suspense, useEffect, useRef, useState } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import {
  OrbitControls,
  Environment,
  ContactShadows,
  Grid,
  Html,
  useProgress,
  Center,
  PerspectiveCamera,
} from "@react-three/drei";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { PLYLoader } from "three/examples/jsm/loaders/PLYLoader.js";

interface Viewer3DProps {
  src: string;
  format: "glb" | "ply";
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
        const hasColor =
          object.attributes.color !== undefined &&
          object.attributes.color.count > 0;
        const material = new THREE.MeshStandardMaterial({
          vertexColors: hasColor,
          color: hasColor ? undefined : 0xcccccc,
          side: THREE.DoubleSide,
          flatShading: false,
          roughness: 0.8,
          metalness: 0.1,
        });
        mesh = new THREE.Mesh(object, material);
        if (!hasColor && (object as any).isBufferGeometry) {
          mesh.geometry.computeVertexNormals();
        }
      } else {
        mesh = object as unknown as THREE.Mesh;
      }

      mesh.castShadow = true;
      mesh.receiveShadow = true;
      group.add(mesh);
      meshRef.current = mesh;

      const box = new THREE.Box3().setFromObject(group);
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

function CameraRig({
  targetBox,
}: {
  targetBox: THREE.Box3 | null;
}) {
  const { camera, controls } = useThree() as {
    camera: THREE.PerspectiveCamera;
    controls: any;
  };

  useEffect(() => {
    if (!targetBox || targetBox.isEmpty()) return;
    const size = new THREE.Vector3();
    targetBox.getSize(size);
    const center = new THREE.Vector3();
    targetBox.getCenter(center);

    const maxDim = Math.max(size.x, size.y, size.z);
    const fov = (camera.fov * Math.PI) / 180;
    const distance = Math.abs(maxDim / 2 / Math.tan(fov / 2)) * 1.6;

    camera.position.set(
      center.x + distance * 0.7,
      center.y + distance * 0.5,
      center.z + distance * 0.7
    );
    camera.near = Math.max(0.01, maxDim / 1000);
    camera.far = Math.max(1000, distance * 20);
    camera.updateProjectionMatrix();

    if (controls?.target) {
      controls.target.copy(center);
      controls.update();
    }
  }, [targetBox, camera, controls]);

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

  const handleBbox = (box: THREE.Box3) => {
    setBbox(box.clone());
  };

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
        shadows
        camera={{ position: [4, 3, 4], fov: 45 }}
        gl={{ antialias: true, alpha: false }}
        style={{ background }}
      >
        <PerspectiveCamera makeDefault position={[4, 3, 4]} fov={45} />
        <color attach="background" args={[background]} />

        <ambientLight intensity={0.6} />
        <directionalLight
          position={[5, 8, 5]}
          intensity={1.2}
          castShadow
          shadow-mapSize-width={2048}
          shadow-mapSize-height={2048}
        />
        <directionalLight position={[-5, 3, -5]} intensity={0.4} color="#a0c4ff" />

        <Suspense fallback={<LoaderScreen />}>
          <Center>
            <group
              onUpdate={(self) => {
                if (wireframe) {
                  self.traverse((o) => {
                    const m = o as THREE.Mesh;
                    if (m.isMesh && m.material) {
                      const mat = m.material as THREE.MeshStandardMaterial;
                      (mat as any).wireframe = true;
                    }
                  });
                } else {
                  self.traverse((o) => {
                    const m = o as THREE.Mesh;
                    if (m.isMesh && m.material) {
                      const mat = m.material as THREE.MeshStandardMaterial;
                      (mat as any).wireframe = false;
                    }
                  });
                }
              }}
            >
              <Model src={src} format={format} onBoundingBox={handleBbox} />
            </group>
          </Center>

          <Environment preset="city" />
          <ContactShadows
            position={[0, -0.01, 0]}
            opacity={0.5}
            scale={10}
            blur={2.5}
            far={4}
          />
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
          <CameraRig targetBox={bbox} />
        </Suspense>

        <OrbitControls
          makeDefault
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
