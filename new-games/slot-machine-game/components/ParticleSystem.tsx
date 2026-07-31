'use client';

import { useEffect, useRef } from 'react';

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  life: number;
  maxLife: number;
  type: 'coin' | 'star' | 'sparkle';
  size: number;
  rotation: number;
  rotationSpeed: number;
}

interface ParticleSystemProps {
  trigger: boolean;
  type: 'win' | 'mega-win';
}

export function ParticleSystem({ trigger, type }: ParticleSystemProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const particlesRef = useRef<Particle[]>([]);
  const animationFrameRef = useRef<number>();

  useEffect(() => {
    if (!trigger) return;

    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;

    // Create particles
    const particleCount = type === 'mega-win' ? 80 : 40;
    const particles: Particle[] = [];

    for (let i = 0; i < particleCount; i++) {
      const angle = (Math.random() * Math.PI * 2);
      const velocity = 2 + Math.random() * 4;
      const particleType: ('coin' | 'star' | 'sparkle') = ['coin', 'star', 'sparkle'][Math.floor(Math.random() * 3)] as any;

      particles.push({
        x: canvas.width / 2,
        y: canvas.height / 2,
        vx: Math.cos(angle) * velocity,
        vy: Math.sin(angle) * velocity - 2,
        life: 1,
        maxLife: 2 + Math.random(),
        type: particleType,
        size: 4 + Math.random() * 8,
        rotation: Math.random() * Math.PI * 2,
        rotationSpeed: (Math.random() - 0.5) * 0.3,
      });
    }

    particlesRef.current = particles;

    const animate = () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      for (let i = particles.length - 1; i >= 0; i--) {
        const p = particles[i];

        p.x += p.vx;
        p.y += p.vy;
        p.vy += 0.15; // gravity
        p.life -= 1 / (60 * p.maxLife);
        p.rotation += p.rotationSpeed;

        if (p.life <= 0) {
          particles.splice(i, 1);
          continue;
        }

        ctx.save();
        ctx.globalAlpha = p.life;
        ctx.translate(p.x, p.y);
        ctx.rotate(p.rotation);

        if (p.type === 'coin') {
          ctx.fillStyle = '#FBBF24';
          ctx.fillRect(-p.size / 2, -p.size / 2, p.size, p.size);
          ctx.strokeStyle = '#D97706';
          ctx.lineWidth = 1;
          ctx.strokeRect(-p.size / 2, -p.size / 2, p.size, p.size);
        } else if (p.type === 'star') {
          drawStar(ctx, 0, 0, 5, p.size / 2, p.size / 4);
        } else {
          ctx.fillStyle = `rgba(255, 215, 0, ${p.life * 0.8})`;
          ctx.beginPath();
          ctx.arc(0, 0, p.size / 2, 0, Math.PI * 2);
          ctx.fill();
        }

        ctx.restore();
      }

      if (particles.length > 0) {
        animationFrameRef.current = requestAnimationFrame(animate);
      }
    };

    animationFrameRef.current = requestAnimationFrame(animate);

    return () => {
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
      }
    };
  }, [trigger, type]);

  const drawStar = (ctx: CanvasRenderingContext2D, cx: number, cy: number, spikes: number, outerRadius: number, innerRadius: number) => {
    ctx.fillStyle = '#8B5CF6';
    ctx.beginPath();
    let step = Math.PI / spikes;
    for (let i = 0; i < Math.PI * 2; i += step) {
      const radius = i % (step * 2) === 0 ? outerRadius : innerRadius;
      const x = cx + Math.cos(i) * radius;
      const y = cy + Math.sin(i) * radius;
      if (i === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    }
    ctx.closePath();
    ctx.fill();
  };

  return (
    <canvas
      ref={canvasRef}
      className="fixed inset-0 pointer-events-none"
    />
  );
}
