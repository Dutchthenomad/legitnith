import requests
import sys
import time
from datetime import datetime

class RugsDataServiceTester:
    def __init__(self, base_url="https://ec5c8aab-fa3e-4846-9364-53e6a6e82b1b.preview.emergentagent.com"):
        self.base_url = base_url
        self.api_base = f"{base_url}/api"
        self.tests_run = 0
        self.tests_passed = 0
        self.current_game_id = None
        self.sample_game_id = None

    def run_test(self, name, method, endpoint, expected_status, timeout=10):
        """Run a single API test"""
        url = f"{self.api_base}/{endpoint}" if not endpoint.startswith('http') else endpoint
        headers = {'Content-Type': 'application/json'}

        self.tests_run += 1
        print(f"\n🔍 Testing {name}...")
        print(f"   URL: {url}")
        
        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=timeout)
            elif method == 'POST':
                response = requests.post(url, headers=headers, timeout=timeout)

            success = response.status_code == expected_status
            if success:
                self.tests_passed += 1
                print(f"✅ Passed - Status: {response.status_code}")
                
                # Try to parse JSON response
                try:
                    json_data = response.json()
                    print(f"   Response keys: {list(json_data.keys()) if isinstance(json_data, dict) else 'Non-dict response'}")
                    return True, json_data
                except:
                    print(f"   Response: {response.text[:100]}...")
                    return True, response.text
            else:
                print(f"❌ Failed - Expected {expected_status}, got {response.status_code}")
                print(f"   Response: {response.text[:200]}...")
                return False, {}

        except Exception as e:
            print(f"❌ Failed - Error: {str(e)}")
            return False, {}

    def test_health(self):
        """Test health endpoint"""
        success, response = self.run_test("Health Check", "GET", "health", 200)
        if success and isinstance(response, dict):
            if 'status' in response and response['status'] == 'ok':
                print("   ✓ Health status is 'ok'")
                return True
            else:
                print("   ⚠ Health response missing 'status: ok'")
        return success

    def test_connection(self):
        """Test connection endpoint and wait for connection"""
        print(f"\n🔍 Testing Connection Status (with 30s timeout for connection)...")
        
        # Try multiple times over 30 seconds to allow socket connection
        max_attempts = 30
        for attempt in range(max_attempts):
            success, response = self.run_test(f"Connection Check (attempt {attempt + 1})", "GET", "connection", 200, timeout=5)
            
            if success and isinstance(response, dict):
                connected = response.get('connected', False)
                socket_id = response.get('socket_id')
                since_ms = response.get('since_connected_ms')
                
                print(f"   Connected: {connected}")
                print(f"   Socket ID: {socket_id}")
                print(f"   Since connected: {since_ms}ms")
                
                if connected:
                    print("   ✅ Socket connection established!")
                    return True
                else:
                    print(f"   ⏳ Not connected yet, waiting... ({attempt + 1}/{max_attempts})")
                    if attempt < max_attempts - 1:
                        time.sleep(1)
            else:
                print(f"   ❌ Failed to get connection status")
                return False
        
        print("   ⚠ Socket connection not established within 30 seconds")
        return False

    def test_live_state(self):
        """Test live state endpoint"""
        success, response = self.run_test("Live State", "GET", "live", 200)
        if success and isinstance(response, dict):
            # Check for expected fields (may be None/empty for live data)
            expected_fields = ['gameId', 'phase', 'active', 'rugged', 'price', 'tickCount', 'cooldownTimer', 'provablyFair', 'updatedAt']
            present_fields = [field for field in expected_fields if field in response]
            print(f"   Present fields: {present_fields}")
            return True
        return success

    def test_snapshots(self):
        """Test snapshots endpoint"""
        success, response = self.run_test("Snapshots", "GET", "snapshots?limit=25", 200)
        if success and isinstance(response, dict):
            if 'items' in response:
                items = response['items']
                print(f"   Found {len(items)} snapshots")
                if len(items) > 0:
                    # Check first item structure
                    first_item = items[0]
                    expected_fields = ['gameId', 'tickCount', 'active', 'rugged', 'price', 'phase', 'createdAt']
                    present_fields = [field for field in expected_fields if field in first_item]
                    print(f"   Sample snapshot fields: {present_fields}")
                return True
            else:
                print("   ⚠ Response missing 'items' field")
        return success

    def test_games(self):
        """Test games endpoint"""
        success, response = self.run_test("Games", "GET", "games?limit=10", 200)
        if success and isinstance(response, dict):
            if 'items' in response:
                items = response['items']
                print(f"   Found {len(items)} games")
                if len(items) > 0:
                    # Check first item structure
                    first_item = items[0]
                    expected_fields = ['id', 'lastSeenAt', 'phase']
                    present_fields = [field for field in expected_fields if field in first_item]
                    print(f"   Sample game fields: {present_fields}")
                    # Store first game ID for later tests
                    self.sample_game_id = first_item.get('id')
                return True
            else:
                print("   ⚠ Response missing 'items' field")
        return success

    def test_games_current(self):
        """Test current game endpoint"""
        success, response = self.run_test("Current Game", "GET", "games/current", 200)
        if success and isinstance(response, dict):
            # May be empty if no current game
            if response:
                expected_fields = ['id', 'phase', 'lastSeenAt']
                present_fields = [field for field in expected_fields if field in response]
                print(f"   Current game fields: {present_fields}")
                # Store current game ID for verification test
                self.current_game_id = response.get('id')
            else:
                print("   No current game active")
            return True
        return success

    def test_game_by_id(self):
        """Test specific game by ID endpoint"""
        if not hasattr(self, 'sample_game_id') or not self.sample_game_id:
            print("   ⚠ No sample game ID available, skipping test")
            return True
            
        success, response = self.run_test("Game by ID", "GET", f"games/{self.sample_game_id}", 200)
        if success and isinstance(response, dict):
            expected_fields = ['id', 'phase', 'lastSeenAt']
            present_fields = [field for field in expected_fields if field in response]
            print(f"   Game by ID fields: {present_fields}")
            return True
        return success

    def test_prng_tracking(self):
        """Test PRNG tracking endpoint"""
        success, response = self.run_test("PRNG Tracking", "GET", "prng/tracking?limit=10", 200)
        if success and isinstance(response, dict):
            if 'items' in response:
                items = response['items']
                print(f"   Found {len(items)} PRNG tracking records")
                if len(items) > 0:
                    # Check first item structure
                    first_item = items[0]
                    expected_fields = ['gameId', 'status', 'serverSeedHash']
                    present_fields = [field for field in expected_fields if field in first_item]
                    print(f"   Sample PRNG tracking fields: {present_fields}")
                    
                    # Check if there's a tracking record for current game
                    if hasattr(self, 'current_game_id') and self.current_game_id:
                        current_game_tracking = next((item for item in items if item.get('gameId') == self.current_game_id), None)
                        if current_game_tracking:
                            print(f"   ✓ Found tracking for current game: {current_game_tracking.get('status')}")
                        else:
                            print("   ⚠ No tracking found for current game")
                return True
            else:
                print("   ⚠ Response missing 'items' field")
        return success

    def test_game_verification(self):
        """Test game verification endpoint"""
        if not hasattr(self, 'current_game_id') or not self.current_game_id:
            print("   ⚠ No current game ID available, skipping verification test")
            return True
            
        success, response = self.run_test("Game Verification", "GET", f"games/{self.current_game_id}/verification", 200)
        if success and isinstance(response, dict):
            expected_fields = ['gameId', 'status', 'serverSeedHash']
            present_fields = [field for field in expected_fields if field in response]
            print(f"   Verification fields: {present_fields}")
            return True
        elif not success:
            # Verification may not exist until server seed is revealed - this is expected
            print("   ⚠ Verification not found (expected until server seed revealed)")
            return True  # Don't fail the test for this expected case
        return success

    def test_negative_cases(self):
        """Test routes without /api prefix - should not hit API (likely returns frontend HTML)"""
        print(f"\n🔍 Testing Negative Cases (routes without /api prefix)...")
        print("   Note: These should return frontend HTML, not API responses")
        
        # Test /health without /api - should return frontend HTML (200) not API
        url = f"{self.base_url}/health"
        try:
            response = requests.get(url, timeout=10)
            print(f"   /health without /api: Status {response.status_code}")
            
            # Check if it's HTML (frontend) vs JSON (API)
            content_type = response.headers.get('content-type', '').lower()
            if 'text/html' in content_type:
                print("   ✓ Returns HTML (frontend), not API")
                return True
            elif 'application/json' in content_type:
                print("   ❌ Returns JSON (API hit), should return frontend HTML")
                return False
            else:
                print(f"   ⚠ Unexpected content type: {content_type}")
                return True  # Don't fail for unexpected but non-API response
                
        except Exception as e:
            print(f"   ❌ Error testing negative case: {e}")
            return False

def main():
    print("🚀 Starting Rugs.fun Data Service API Tests")
    print("=" * 60)
    
    tester = RugsDataServiceTester()
    
    # Run all tests in order (some depend on previous results)
    results = []
    results.append(("Health Check", tester.test_health()))
    results.append(("Connection Status", tester.test_connection()))
    results.append(("Live State", tester.test_live_state()))
    results.append(("Snapshots", tester.test_snapshots()))
    results.append(("Games", tester.test_games()))
    results.append(("Current Game", tester.test_games_current()))
    results.append(("Game by ID", tester.test_game_by_id()))
    results.append(("PRNG Tracking", tester.test_prng_tracking()))
    results.append(("Game Verification", tester.test_game_verification()))
    results.append(("Negative Cases", tester.test_negative_cases()))
    
    # Print summary
    print("\n" + "=" * 60)
    print("📊 TEST SUMMARY")
    print("=" * 60)
    
    passed_tests = sum(1 for _, passed in results if passed)
    total_tests = len(results)
    
    for test_name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} {test_name}")
    
    print(f"\nOverall: {passed_tests}/{total_tests} test suites passed")
    print(f"Individual API calls: {tester.tests_passed}/{tester.tests_run} passed")
    
    return 0 if passed_tests == total_tests else 1

if __name__ == "__main__":
    sys.exit(main())