import { useEffect, useRef, useState } from 'react'

/**
 * The rotating DNA helix on the landing page.
 *
 * Adapted from the Medico prototype's Three.js scene, with three changes that matter for a
 * page people actually land on:
 *
 * **Three.js is imported dynamically.** It is roughly half a megabyte and the model is nine
 * more; loading them eagerly would hold up the first paint of a page whose job is to get
 * someone to the login screen. The hero renders immediately and the helix arrives when it
 * arrives.
 *
 * **It gives up quietly.** No WebGL, a failed model fetch, or a machine that would rather
 * not run a render loop — all of them end with the decorative panel simply not appearing.
 * A marketing flourish must never be the reason a page looks broken.
 *
 * **It respects `prefers-reduced-motion`.** A continuously spinning object is exactly what
 * that setting is asking about, so when it is set the scene is skipped entirely.
 */
export default function DnaScene() {
  const mount = useRef(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    if (reduced) return

    const container = mount.current
    if (!container) return

    let disposed = false
    let frame = 0
    let renderer

    ;(async () => {
      try {
        const THREE = await import('three')
        const { GLTFLoader } = await import('three/examples/jsm/loaders/GLTFLoader.js')

        if (disposed) return

        const scene = new THREE.Scene()

        const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100)
        camera.position.z = 4

        renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true })
        renderer.setSize(520, 520)
        // Capped: on a high-density display the uncapped ratio quadruples the pixels for
        // a decoration, and the fans notice before the eyes do.
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
        renderer.domElement.style.pointerEvents = 'none'
        renderer.domElement.style.maxWidth = '100%'
        renderer.domElement.style.height = 'auto'
        container.appendChild(renderer.domElement)

        // The same two lights as the prototype: the product's teal and its purple.
        const teal = new THREE.PointLight(0x17afa2, 8, 10)
        teal.position.set(3, 3, 3)
        scene.add(teal)

        const purple = new THREE.PointLight(0x8b5cf6, 5, 10)
        purple.position.set(-3, 2, 2)
        scene.add(purple)

        scene.add(new THREE.AmbientLight(0xffffff, 1))

        let helix = null

        new GLTFLoader().load(
          `${import.meta.env.BASE_URL}models/dna.glb`,
          (gltf) => {
            if (disposed) return
            helix = gltf.scene
            helix.scale.set(4, 4, 4)
            helix.rotation.set(
              THREE.MathUtils.degToRad(-10),
              THREE.MathUtils.degToRad(25),
              THREE.MathUtils.degToRad(5),
            )
            helix.position.y = -0.3
            scene.add(helix)
          },
          undefined,
          () => setFailed(true),
        )

        const tick = () => {
          if (disposed) return
          frame = requestAnimationFrame(tick)
          if (helix) helix.rotation.y += 0.0035
          renderer.render(scene, camera)
        }

        tick()
      } catch {
        setFailed(true)
      }
    })()

    return () => {
      disposed = true
      cancelAnimationFrame(frame)
      // Release the GL context rather than waiting for garbage collection — browsers cap
      // how many can exist at once, and navigating back and forth would exhaust them.
      renderer?.dispose?.()
      renderer?.domElement?.remove?.()
    }
  }, [])

  if (failed) return null

  return <div className="dna-scene" ref={mount} aria-hidden="true" />
}
