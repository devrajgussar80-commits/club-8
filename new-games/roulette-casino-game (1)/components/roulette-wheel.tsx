'use client'

import { Canvas, useFrame } from '@react-three/fiber'
import { ContactShadows, Environment } from '@react-three/drei'
import { useEffect, useMemo, useRef } from 'react'
import * as THREE from 'three'

const wheelOrder = [0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8, 23, 10, 5, 24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7, 28, 12, 35, 3, 26]
const redNumbers = new Set([1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36])
const step = (Math.PI * 2) / 37

function createWoodTexture() {
  const canvas = document.createElement('canvas')
  canvas.width = 1024
  canvas.height = 1024
  const context = canvas.getContext('2d')
  if (!context) return new THREE.CanvasTexture(canvas)

  context.fillStyle = '#6b210f'
  context.fillRect(0, 0, 1024, 1024)
  const center = 512
  for (let ring = 0; ring < 42; ring += 1) {
    context.beginPath()
    const radius = 24 + ring * 15
    for (let angle = 0; angle <= Math.PI * 2 + 0.05; angle += 0.05) {
      const ripple = Math.sin(angle * 7 + ring * 0.8) * 5 + Math.sin(angle * 17) * 2
      const x = center + Math.cos(angle) * (radius + ripple)
      const y = center + Math.sin(angle) * (radius * 0.72 + ripple)
      if (angle === 0) context.moveTo(x, y)
      else context.lineTo(x, y)
    }
    context.closePath()
    context.strokeStyle = ring % 3 === 0 ? 'rgba(40,8,3,.34)' : 'rgba(226,105,43,.16)'
    context.lineWidth = ring % 4 === 0 ? 4 : 2
    context.stroke()
  }
  for (let i = 0; i < 140; i += 1) {
    const x = (i * 83) % 1024
    const y = (i * 151) % 1024
    context.fillStyle = i % 2 ? 'rgba(255,160,78,.08)' : 'rgba(35,6,2,.12)'
    context.beginPath()
    context.ellipse(x, y, 2 + (i % 5), 12 + (i % 13), i * 0.31, 0, Math.PI * 2)
    context.fill()
  }
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  texture.anisotropy = 12
  return texture
}

function NumberLabel({ number, radius = 3.05 }: { number: number; radius?: number }) {
  const texture = useMemo(() => {
    const canvas = document.createElement('canvas')
    canvas.width = 192
    canvas.height = 128
    const context = canvas.getContext('2d')
    if (context) {
      context.clearRect(0, 0, canvas.width, canvas.height)
      context.font = '900 78px Arial, sans-serif'
      context.textAlign = 'center'
      context.textBaseline = 'middle'
      context.lineWidth = 10
      context.strokeStyle = '#35110b'
      context.strokeText(String(number), 96, 66)
      context.fillStyle = '#f4e5c6'
      context.fillText(String(number), 96, 66)
    }
    const canvasTexture = new THREE.CanvasTexture(canvas)
    canvasTexture.colorSpace = THREE.SRGBColorSpace
    canvasTexture.anisotropy = 12
    return canvasTexture
  }, [number])

  useEffect(() => () => texture.dispose(), [texture])

  return (
    <mesh position={[0, 0.715, -radius]} rotation={[-Math.PI / 2, 0, 0]}>
      <planeGeometry args={[0.42, 0.28]} />
      <meshBasicMaterial map={texture} transparent toneMapped={false} depthWrite={false} />
    </mesh>
  )
}

function WheelModel({ rotation, ballRotation, isSpinning }: { rotation: number; ballRotation: number; isSpinning: boolean }) {
  const wheel = useRef<THREE.Group>(null)
  const ball = useRef<THREE.Group>(null)
  const targetWheel = useRef(0)
  const targetBall = useRef(0)
  const woodTexture = useMemo(() => createWoodTexture(), [])

  useEffect(() => () => woodTexture.dispose(), [woodTexture])
  useEffect(() => { targetWheel.current = THREE.MathUtils.degToRad(rotation) }, [rotation])
  useEffect(() => { targetBall.current = THREE.MathUtils.degToRad(ballRotation) }, [ballRotation])

  useFrame((state, delta) => {
    if (wheel.current) {
      wheel.current.rotation.y = THREE.MathUtils.damp(wheel.current.rotation.y, targetWheel.current, isSpinning ? 0.75 : 3, delta)
      wheel.current.position.y = Math.sin(state.clock.elapsedTime * 0.7) * 0.015
    }
    if (ball.current) {
      ball.current.rotation.y = THREE.MathUtils.damp(ball.current.rotation.y, targetBall.current, isSpinning ? 0.68 : 3, delta)
      ball.current.position.y = isSpinning ? 0.58 + Math.sin(state.clock.elapsedTime * 12) * 0.025 : 0.5
    }
  })

  return (
    <group rotation={[0.04, 0, 0]}>
      <group ref={wheel}>
        <mesh castShadow receiveShadow>
          <cylinderGeometry args={[3.88, 4.05, 0.5, 128]} />
          <meshPhysicalMaterial map={woodTexture} color="#5b180b" roughness={0.27} metalness={0.04} clearcoat={0.72} clearcoatRoughness={0.2} />
        </mesh>
        <mesh position={[0, 0.3, 0]} castShadow>
          <cylinderGeometry args={[3.72, 3.78, 0.16, 128]} />
          <meshPhysicalMaterial map={woodTexture} color="#a33a16" roughness={0.24} metalness={0.03} clearcoat={0.82} clearcoatRoughness={0.16} />
        </mesh>
        <mesh position={[0, 0.55, 0]} castShadow>
          <torusGeometry args={[3.46, 0.12, 20, 128]} />
          <meshPhysicalMaterial color="#bd7b22" metalness={0.92} roughness={0.2} clearcoat={0.35} clearcoatRoughness={0.15} />
        </mesh>
        <mesh position={[0, 0.42, 0]}>
          <cylinderGeometry args={[3.36, 3.36, 0.15, 128]} />
          <meshPhysicalMaterial map={woodTexture} color="#8b2c12" roughness={0.25} clearcoat={0.75} clearcoatRoughness={0.18} />
        </mesh>
        <mesh position={[0, 0.59, 0]}><torusGeometry args={[2.29, 0.075, 16, 128]} /><meshStandardMaterial color="#d0a154" metalness={0.9} roughness={0.17} /></mesh>
        {Array.from({ length: 12 }, (_, index) => {
          const angle = index * Math.PI / 6
          return <mesh key={`spoke-${index}`} position={[Math.sin(angle) * 1.13, 0.675, Math.cos(angle) * 1.13]} rotation={[0, angle, 0]}><boxGeometry args={[0.022, 0.025, 2.15]} /><meshStandardMaterial color="#2d0b05" roughness={0.45} /></mesh>
        })}
        {Array.from({ length: 8 }, (_, index) => {
          const angle = index * Math.PI / 4
          return <mesh key={`marker-${index}`} position={[Math.sin(angle) * 3.58, 0.58, Math.cos(angle) * 3.58]} rotation={[0, -angle, Math.PI / 2]} castShadow><coneGeometry args={[0.1, 0.34, 4]} /><meshStandardMaterial color="#c68b35" metalness={0.88} roughness={0.22} /></mesh>
        })}
        {wheelOrder.map((number, index) => {
          const angle = index * step
          const color = number === 0 ? '#12633d' : redNumbers.has(number) ? '#d9271b' : '#120e0b'
          return (
            <group key={number} rotation={[0, angle, 0]}>
              <mesh position={[0, 0.565, -2.63]} rotation={[Math.PI / 2, 0, 0]} castShadow>
                <boxGeometry args={[0.43, 0.76, 0.12]} />
                <meshStandardMaterial color={color} roughness={0.34} metalness={0.08} />
              </mesh>
              <mesh position={[0, 0.635, -3.14]} rotation={[Math.PI / 2, 0, 0]} castShadow>
                <boxGeometry args={[0.47, 0.34, 0.1]} />
                <meshStandardMaterial color={color} roughness={0.3} />
              </mesh>
              <mesh position={[0.225, 0.66, -2.82]}>
                <boxGeometry args={[0.025, 0.18, 1.04]} />
                <meshStandardMaterial color="#c99a45" metalness={0.92} roughness={0.15} />
              </mesh>
              <NumberLabel number={number} />
            </group>
          )
        })}
        <mesh position={[0, 0.55, 0]} castShadow>
          <cylinderGeometry args={[1.78, 2.3, 0.22, 96]} />
          <meshStandardMaterial color="#7a2413" roughness={0.25} metalness={0.08} />
        </mesh>
        <mesh position={[0, 0.68, 0]}><torusGeometry args={[2.08, 0.08, 16, 96]} /><meshStandardMaterial color="#c99a45" metalness={0.94} roughness={0.14} /></mesh>
        <mesh position={[0, 0.85, 0]} castShadow><cylinderGeometry args={[0.44, 0.62, 0.55, 48]} /><meshStandardMaterial color="#a97828" metalness={0.88} roughness={0.19} /></mesh>
        <mesh position={[0, 1.23, 0]} castShadow><sphereGeometry args={[0.34, 40, 40]} /><meshStandardMaterial color="#c99a45" metalness={0.92} roughness={0.15} /></mesh>
        <mesh position={[0, 1.58, 0]} castShadow><cylinderGeometry args={[0.25, 0.38, 0.48, 40]} /><meshStandardMaterial color="#a97828" metalness={0.9} roughness={0.16} /></mesh>
        <mesh position={[0, 1.87, 0]} castShadow><cylinderGeometry args={[0.42, 0.28, 0.16, 40]} /><meshStandardMaterial color="#c99a45" metalness={0.94} roughness={0.14} /></mesh>
      </group>
      <group ref={ball}><mesh position={[0, 0.72, -3.48]} castShadow><sphereGeometry args={[0.14, 32, 32]} /><meshStandardMaterial color="#f4e5c6" metalness={0.05} roughness={0.2} /></mesh></group>
      <ContactShadows position={[0, -0.36, 0]} opacity={0.68} scale={10} blur={2.2} far={6} />
    </group>
  )
}

export function RouletteWheel(props: { rotation: number; ballRotation: number; isSpinning: boolean; winner: number | null }) {
  return (
    <div className="wheel-stage relative mx-auto aspect-[4/3] w-full max-w-xl overflow-hidden rounded-3xl" role="img" aria-label={props.isSpinning ? 'Three-dimensional roulette wheel spinning' : `Three-dimensional roulette wheel${props.winner !== null ? ` showing ${props.winner}` : ''}`}>
      <Canvas dpr={[1, 1.8]} shadows camera={{ position: [0, 8.7, 6.3], fov: 37 }} gl={{ antialias: true, alpha: true }}>
        <ambientLight intensity={0.48} />
        <spotLight position={[3, 10, 5]} intensity={115} angle={0.48} penumbra={0.72} castShadow color="#f4e5c6" />
        <pointLight position={[-5, 4, -3]} intensity={30} color="#d17b35" />
        <pointLight position={[4, 3, 2]} intensity={20} color="#c99a45" />
        <WheelModel rotation={props.rotation} ballRotation={props.ballRotation} isSpinning={props.isSpinning} />
        <Environment preset="studio" environmentIntensity={0.35} />
      </Canvas>
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-px bg-primary/40" />
    </div>
  )
}

export { wheelOrder, redNumbers }
