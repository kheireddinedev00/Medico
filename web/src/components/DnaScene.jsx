import { useEffect, useRef, useState } from 'react'

/**
 * The rotating DNA helix and its platform, on the landing page.
 *
 * The prototype's scene, faithfully: the helix scaled ×4 and tilted (−10° z, 25° x, 5° y),
 * the platform dropped to y = −1 and turned a quarter turn, both spinning slowly on their
 * own axes in the same direction, and the helix bobbing on a sine. Where the model carries
 * its own animation clip, that plays too — a mixer driven by a clock, so the strand
 * unwinds rather than only turning.
 *
 * Three departures, all about a page people actually land on rather than a mockup:
 *
 * **Three.js and the models load dynamically.** Half a megabyte of library and eleven of
 * geometry; loading them eagerly would hold up the first paint of a page whose job is to
 * get someone to the sign-in screen. The hero renders immediately and the scene arrives
 * when it arrives.
 *
 * **It gives up quietly.** No WebGL, a failed fetch, a machine that would rather not run a
 * render loop — all of them end with the panel simply not appearing. A decoration must
 * never be the reason a page looks broken.
 *
 * **It respects `prefers-reduced-motion`.** A continuously spinning object is exactly what
 * that setting is about, so the scene is skipped entirely when it is set.
 */
export default function DnaScene() {
  const mount = useRef(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return

    const container = mount.current
    if (!container) return

    let disposed = false
    let frame = 0
    let renderer
    let mixer

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

        // The prototype's two lights: the product's teal and its purple.
        const teal = new THREE.PointLight(0x17afa2, 8, 10)
        teal.position.set(3, 3, 3)
        scene.add(teal)

        const purple = new THREE.PointLight(0x8b5cf6, 5, 10)
        purple.position.set(-3, 2, 2)
        scene.add(purple)

        scene.add(new THREE.AmbientLight(0xffffff, 1))

        const loader = new GLTFLoader()
        const clock = new THREE.Clock()
        const base = import.meta.env.BASE_URL

        let helix = null
        let platform = null

        loader.load(`${base}models/dna.glb`, (gltf) => {
          if (disposed) return

          helix = gltf.scene
          helix.scale.set(4, 4, 4)
          helix.rotation.z = THREE.MathUtils.degToRad(-10)
          helix.rotation.x = THREE.MathUtils.degToRad(25)
          helix.rotation.y = THREE.MathUtils.degToRad(5)
          helix.position.y = 0.3
          scene.add(helix)

          // The strand unwinds where the model brought its own clip.
          if (gltf.animations.length > 0) {
            mixer = new THREE.AnimationMixer(helix)
            mixer.clipAction(gltf.animations[0]).play()
          }
        }, undefined, () => setFailed(true))

        // The base the helix stands on. Its own file, and its own failure: a missing
        // platform leaves the helix floating rather than taking the whole scene down.
        loader.load(`${base}models/platform.glb`, (gltf) => {
          if (disposed) return

          platform = gltf.scene
          platform.position.set(0, -1, 0)
          platform.rotation.y = Math.PI / 2
          scene.add(platform)
        }, undefined, () => { /* the helix is the point; carry on without the base */ })

        const tick = () => {
          if (disposed) return
          frame = requestAnimationFrame(tick)

          const delta = clock.getDelta()
          if (mixer) mixer.update(delta)

          if (helix) {
            helix.rotation.y += 0.003
            // The float. Driven by the wall clock rather than accumulated frames, so it
            // keeps its rhythm through a dropped frame or a backgrounded tab.
            helix.position.y = Math.sin(Date.now() * 0.002) * 0.08
          }

          if (platform) platform.rotation.y += 0.005

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
      mixer?.stopAllAction?.()
      // Release the GL context rather than waiting for garbage collection — browsers cap
      // how many can exist at once, and navigating back and forth would exhaust them.
      renderer?.dispose?.()
      renderer?.domElement?.remove?.()
    }
  }, [])

  if (failed) return null

  return <div className="dna-scene" ref={mount} aria-hidden="true" />
}
