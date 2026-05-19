'use client'

import { Mail, MapPin, Phone } from 'lucide-react'

export function Footer() {
  return (
    <footer className="bg-gray-900 text-gray-300">
      <div className="max-w-7xl mx-auto px-4 sm:px-8 py-12">
        <div className="grid md:grid-cols-3 gap-8">
          {/* Company Info */}
          <div>
            <div className="flex items-center gap-3 mb-4">
              {/* Simple logo placeholder - no image import needed */}
              <div className="h-8 w-8 bg-[#1A7070] rounded-lg flex items-center justify-center text-white font-bold text-sm">
                M
              </div>
              <h3 className="text-white font-semibold">MITRAM360</h3>
            </div>
            <p className="text-sm text-gray-400 leading-relaxed">
              Your trusted platform for discovering verified NBFC lending partners across India.
            </p>
          </div>
          
          {/* Legal */}
          <div>
            <h4 className="text-white font-medium mb-4">Legal</h4>
            <ul className="space-y-2 text-sm text-gray-500">
              <li>Privacy Policy</li>
              <li>Terms of Service</li>
              <li>Data Security</li>
              <li>RBI Compliance</li>
            </ul>
          </div>
          
          {/* Contact */}
          <div>
            <h4 className="text-white font-medium mb-4">Contact</h4>
            <ul className="space-y-3 text-sm">
              <li className="flex items-center gap-2">
                <Mail className="w-4 h-4" />
                <span>info@mitram360.com</span>
              </li>
              <li className="flex items-center gap-2">
                <Phone className="w-4 h-4" />
                <span>+91 9589516033</span>
              </li>
              <li className="flex items-center gap-2">
                <MapPin className="w-4 h-4" />
                <span>Indore, India</span>
              </li>
            </ul>
          </div>
        </div>
        
        {/* Bottom Bar */}
        <div className="border-t border-gray-800 mt-8 pt-8 text-sm text-center text-gray-400">
          <p>© 2026 MITRAM360. All rights reserved.</p>
        </div>
      </div>
    </footer>
  )
}
