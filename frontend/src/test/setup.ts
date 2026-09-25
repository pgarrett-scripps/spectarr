import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { clearResourceCache } from '../api/resourceCache'

afterEach(clearResourceCache)
